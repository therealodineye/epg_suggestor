class Plugin:
    name = "EPG Suggester"
    version = "1.7.0"
    description = "Suggests EPG entries for channels without EPG assigned, using fuzzy name matching."

    def run(self, action, params, context):
        import logging
        log = context.get("logger") or logging.getLogger("plugins.epg_suggester")
        settings = context.get("settings", {})
        cfg = self._parse_settings(settings)
        log.info("EPG Suggester: action=%s", action)

        if action == "show_unmatched":
            return self._show_unmatched(cfg, log)
        elif action == "scan_and_suggest":
            return self._scan(cfg, log)
        elif action == "export_suggestions_csv":
            return self._export(cfg, log)
        elif action == "apply_suggestions":
            return self._apply(cfg, log)
        else:
            return {"status": "error", "message": "Unknown action: " + action}

    def _parse_settings(self, settings):
        return {
            "geo":    bool(settings.get("ignore_geo_prefixes", True)),
            "qual":   bool(settings.get("ignore_quality_tags", True)),
            "misc":   bool(settings.get("ignore_misc_tags", True)),
            "min_s":  max(0, min(100, int(settings.get("min_score", 50)))),
            "max_n":  max(1, min(10,  int(settings.get("max_suggestions", 3)))),
            "sf":     [x.strip() for x in settings.get("epg_sources_filter", "").split(",") if x.strip()],
            "gf":     [x.strip() for x in settings.get("group_filter", "").split(",") if x.strip()],
            "auto":   bool(settings.get("auto_apply", False)),
            "thresh": max(0, min(100, int(settings.get("auto_apply_threshold", 85)))),
        }

    def _norm(self, name, cfg):
        import re
        n = name.strip()
        if cfg["geo"]:
            n = re.sub(r"^(?:[A-Z]{2,4}\s*[|\-:]\s*|(?:USA?|UK|AU|CA|DE|FR|IT|ES|NL|BE|CH|AT)\s*[|\-:]\s*)", "", n, flags=re.IGNORECASE).strip()
        if cfg["qual"]:
            n = re.sub(r"\b(?:4k|uhd|fhd|hd|sd|hevc|h265|h264|hdr|sdr|1080[pi]?|720[pi]?)\b", " ", n, flags=re.IGNORECASE)
        if cfg["misc"]:
            n = re.sub(r"\b(?:vip|backup\d*|bkup|plus|premium|extra|alt|\+1|\+2)\b|\([^)]{0,15}\)|\[[^\]]{0,15}\]|\s*\*+\s*", " ", n, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", n).strip().lower()

    def _score(self, a, b):
        import difflib
        if not a or not b:
            return 0
        if a == b:
            return 100
        s = int(difflib.SequenceMatcher(None, " ".join(sorted(a.split())), " ".join(sorted(b.split()))).ratio() * 90)
        sa, sb = set(a.split()), set(b.split())
        if sa and sb:
            s = max(s, int(len(sa & sb) / max(len(sa), len(sb)) * 60))
        if a in b or b in a:
            s = min(99, s + 20)
        return min(s, 99)

    def _get_data(self, cfg, log):
        from apps.channels.models import Channel
        from apps.epg.models import EPGData
        qs = Channel.objects.select_related("channel_group").filter(epg_data__isnull=True)
        if cfg["gf"]:
            qs = qs.filter(channel_group__name__in=cfg["gf"])
        channels = list(qs.values("id", "name", "channel_group__name"))
        log.info("EPG Suggester: %d unmatched channels", len(channels))
        epg_qs = EPGData.objects.select_related("epg_source").values("id", "name", "tvg_id", "epg_source__name")
        if cfg["sf"]:
            epg_qs = epg_qs.filter(epg_source__name__in=cfg["sf"])
        index = []
        for e in epg_qs:
            raw = (e["name"] or "").strip()
            if raw:
                index.append({"id": e["id"], "name": raw, "tvg_id": e["tvg_id"] or "",
                               "source": e["epg_source__name"] or "", "norm": self._norm(raw, cfg)})
        log.info("EPG Suggester: %d EPG entries in index", len(index))
        return channels, index

    def _suggest(self, ch_norm, index, cfg):
        scored = [dict(e, score=self._score(ch_norm, e["norm"])) for e in index if self._score(ch_norm, e["norm"]) >= cfg["min_s"]]
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:cfg["max_n"]]

    def _show_unmatched(self, cfg, log):
        from apps.channels.models import Channel
        qs = Channel.objects.select_related("channel_group").filter(epg_data__isnull=True)
        if cfg["gf"]:
            qs = qs.filter(channel_group__name__in=cfg["gf"])
        channels = list(qs.values("id", "name", "channel_group__name").order_by("channel_group__name", "name"))
        if not channels:
            return "All channels already have EPG assigned!"
        lines = [str(len(channels)) + " channels without EPG:\n"]
        grp = None
        for c in channels:
            g = c.get("channel_group__name") or "No Group"
            if g != grp:
                lines.append("\n[" + g + "]")
                grp = g
            lines.append("  id=" + str(c["id"]) + "  " + (c["name"] or ""))
        return "\n".join(lines)

    def _scan(self, cfg, log):
        import os
        from datetime import datetime
        channels, index = self._get_data(cfg, log)
        lines = ["EPG Suggester - Scan Results", str(len(channels)) + " channels without EPG", ""]
        matched = 0
        for ch in channels:
            raw = ch["name"] or ""
            n = self._norm(raw, cfg)
            sugg = self._suggest(n, index, cfg)
            if sugg:
                matched += 1
            lines.append("---")
            lines.append("Channel: " + raw + "  [" + (ch.get("channel_group__name") or "") + "]")
            lines.append("  Normalised: " + n)
            if sugg:
                for i, s in enumerate(sugg, 1):
                    lines.append("  [" + str(i) + "] score=" + str(s["score"]) + "  " + s["name"] + "  source=" + s["source"] + "  epg_id=" + str(s["id"]))
            else:
                lines.append("  No suggestions above score " + str(cfg["min_s"]))
        lines += ["---", "Matched: " + str(matched) + " / " + str(len(channels))]
        os.makedirs("/data/exports", exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = "/data/exports/epg_suggester_scan_" + ts + ".txt"
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        log.info("EPG Suggester: scan saved to %s", out)
        # Return first 50 lines to UI, full results in file
        preview = "\n".join(lines[:50])
        if len(lines) > 50:
            preview += "\n\n... (truncated, full results in " + out + ")"
        return preview

    def _export(self, cfg, log):
        import csv, os
        from datetime import datetime
        channels, index = self._get_data(cfg, log)
        os.makedirs("/data/exports", exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = "/data/exports/epg_suggester_" + ts + ".csv"
        matched = 0
        with open(path, "w", newline="", encoding="utf-8") as fh:
            fh.write("# EPG Suggester | " + datetime.now().isoformat() + "\n")
            fh.write("# min_score=" + str(cfg["min_s"]) + "  max_suggestions=" + str(cfg["max_n"]) + "\n#\n")
            w = csv.writer(fh)
            w.writerow(["channel_id", "channel_name", "channel_norm", "channel_group",
                        "rank", "score", "epg_name", "tvg_id", "epg_source", "epg_data_id"])
            for ch in channels:
                raw = ch["name"] or ""
                n = self._norm(raw, cfg)
                sugg = self._suggest(n, index, cfg)
                if sugg:
                    matched += 1
                    for rank, s in enumerate(sugg, 1):
                        w.writerow([ch["id"], raw, n, ch.get("channel_group__name") or "",
                                    rank, s["score"], s["name"], s["tvg_id"], s["source"], s["id"]])
                else:
                    w.writerow([ch["id"], raw, n, ch.get("channel_group__name") or "",
                                "", "", "NO_MATCH", "", "", ""])
        log.info("EPG Suggester: CSV saved to %s  (%d/%d matched)", path, matched, len(channels))
        return "CSV saved to " + path + "\nMatched: " + str(matched) + " / " + str(len(channels)) + "\n\nCopy with:\n  docker cp dispatcharr:" + path + " ./"

    def _apply(self, cfg, log):
        from apps.channels.models import Channel
        if not cfg["auto"]:
            return "Auto-Apply is DISABLED. Enable it in settings first."
        channels, index = self._get_data(cfg, log)
        applied = skipped = failed = 0
        for ch in channels:
            raw = ch["name"] or ""
            sugg = self._suggest(self._norm(raw, cfg), index, cfg)
            if not sugg or sugg[0]["score"] < cfg["thresh"]:
                skipped += 1
                continue
            try:
                Channel.objects.filter(pk=ch["id"]).update(epg_data_id=sugg[0]["id"])
                log.info("EPG Suggester: APPLY  %s -> %s (score=%d)", raw, sugg[0]["name"], sugg[0]["score"])
                applied += 1
            except Exception as e:
                log.error("EPG Suggester: FAIL  %s -> %s", raw, e)
                failed += 1
        return "Applied: " + str(applied) + "  Skipped: " + str(skipped) + "  Failed: " + str(failed)
