import csv
import difflib
import logging
import os
import re
from datetime import datetime

logger = logging.getLogger("plugins.epg_suggester")

_GEO_RE = re.compile(
    r"^(?:[A-Z]{2,4}\s*[|\-:]\s*|(?:USA?|UK|AU|CA|DE|FR|IT|ES|NL|BE|CH|AT)\s*[|\-:]\s*)",
    re.IGNORECASE,
)
_QUALITY_RE = re.compile(
    r"\b(?:4k|uhd|fhd|hd|sd|hevc|h265|h264|hdr|sdr|1080[pi]?|720[pi]?)\b",
    re.IGNORECASE,
)
_MISC_RE = re.compile(
    r"\b(?:vip|backup\d*|bkup|plus|premium|extra|alt|\+1|\+2)\b"
    r"|[\[\(][a-z0-9 ]{0,10}[\]\)]|\s*\*+\s*",
    re.IGNORECASE,
)
_WS_RE = re.compile(r"\s+")


def _norm(name, geo, quality, misc):
    n = name.strip()
    if geo:
        n = _GEO_RE.sub("", n).strip()
    if quality:
        n = _QUALITY_RE.sub(" ", n)
    if misc:
        n = _MISC_RE.sub(" ", n)
    return _WS_RE.sub(" ", n).strip().lower()


def _score(a, b):
    if not a or not b:
        return 0
    if a == b:
        return 100
    ratio = difflib.SequenceMatcher(None, " ".join(sorted(a.split())), " ".join(sorted(b.split()))).ratio()
    score = int(ratio * 90)
    sa, sb = set(a.split()), set(b.split())
    if sa and sb:
        score = max(score, int(len(sa & sb) / max(len(sa), len(sb)) * 60))
    if a in b or b in a:
        score = min(99, score + 20)
    return min(score, 99)


class Plugin:
    name = "EPG Suggester"
    version = "1.3.0"
    description = "Suggests EPG entries for channels without EPG assigned, using fuzzy name matching."

    def run(self, action, params, context):
        log = context.get("logger", logger)
        settings = context.get("settings", {})

        geo     = bool(settings.get("ignore_geo_prefixes", True))
        quality = bool(settings.get("ignore_quality_tags", True))
        misc    = bool(settings.get("ignore_misc_tags", True))
        min_s   = max(0, min(100, int(settings.get("min_score", 50))))
        max_n   = max(1, min(10,  int(settings.get("max_suggestions", 3))))
        sf      = [x.strip() for x in settings.get("epg_sources_filter", "").split(",") if x.strip()]
        gf      = [x.strip() for x in settings.get("group_filter", "").split(",") if x.strip()]
        auto    = bool(settings.get("auto_apply", False))
        thresh  = max(0, min(100, int(settings.get("auto_apply_threshold", 85))))

        log.info("EPG Suggester: action=%s", action)

        if action == "show_unmatched":
            return self._show_unmatched(gf, log)
        elif action == "scan_and_suggest":
            return self._scan(geo, quality, misc, min_s, max_n, sf, gf, log)
        elif action == "export_suggestions_csv":
            return self._export(geo, quality, misc, min_s, max_n, sf, gf, log)
        elif action == "apply_suggestions":
            return self._apply(geo, quality, misc, min_s, max_n, sf, gf, auto, thresh, log)
        else:
            return {"status": "error", "message": "Unknown action: " + action}

    # ------------------------------------------------------------------ internals

    def _get_data(self, gf, sf, geo, quality, misc, log):
        from apps.channels.models import Channel
        from apps.epg.models import EPGData

        qs = Channel.objects.select_related("channel_group").filter(epg_data__isnull=True)
        if gf:
            qs = qs.filter(channel_group__name__in=gf)
        channels = list(qs.values("id", "name", "channel_group__name"))
        log.info("EPG Suggester: %d unmatched channels", len(channels))

        epg_qs = EPGData.objects.select_related("epg_source").values("id", "name", "tvg_id", "epg_source__name")
        if sf:
            epg_qs = epg_qs.filter(epg_source__name__in=sf)

        index = []
        for e in epg_qs:
            raw = (e["name"] or "").strip()
            if raw:
                index.append({
                    "id": e["id"],
                    "name": raw,
                    "tvg_id": e["tvg_id"] or "",
                    "source": e["epg_source__name"] or "",
                    "norm": _norm(raw, geo, quality, misc),
                })
        log.info("EPG Suggester: %d EPG entries in index", len(index))
        return channels, index

    def _suggest(self, ch_norm, index, min_s, max_n):
        scored = sorted(
            [dict(e, score=_score(ch_norm, e["norm"])) for e in index if _score(ch_norm, e["norm"]) >= min_s],
            key=lambda x: x["score"], reverse=True
        )
        return scored[:max_n]

    def _show_unmatched(self, gf, log):
        from apps.channels.models import Channel
        qs = Channel.objects.select_related("channel_group").filter(epg_data__isnull=True)
        if gf:
            qs = qs.filter(channel_group__name__in=gf)
        channels = list(qs.values("id", "name", "channel_group__name").order_by("channel_group__name", "name"))
        if not channels:
            return "All channels have EPG assigned!"
        lines = [str(len(channels)) + " channels without EPG:\n"]
        grp = None
        for c in channels:
            g = c.get("channel_group__name") or "No Group"
            if g != grp:
                lines.append("\n[" + g + "]")
                grp = g
            lines.append("  id=" + str(c["id"]) + "  " + (c["name"] or ""))
        return "\n".join(lines)

    def _scan(self, geo, quality, misc, min_s, max_n, sf, gf, log):
        channels, index = self._get_data(gf, sf, geo, quality, misc, log)
        lines = ["EPG Suggester Scan Results", "Channels without EPG: " + str(len(channels)), ""]
        matched = 0
        for ch in channels:
            raw = ch["name"] or ""
            norm = _norm(raw, geo, quality, misc)
            sugg = self._suggest(norm, index, min_s, max_n)
            if sugg:
                matched += 1
            lines.append("---")
            lines.append("Channel: " + raw + "  [" + (ch.get("channel_group__name") or "") + "]")
            lines.append("  Normalised: " + norm)
            if sugg:
                for i, s in enumerate(sugg, 1):
                    lines.append("  [" + str(i) + "] score=" + str(s["score"]) + "  " + s["name"] + "  (tvg_id=" + s["tvg_id"] + "  source=" + s["source"] + "  id=" + str(s["id"]) + ")")
            else:
                lines.append("  No suggestions above threshold " + str(min_s))
        lines.append("---")
        lines.append("Matched: " + str(matched) + " / " + str(len(channels)))
        return "\n".join(lines)

    def _export(self, geo, quality, misc, min_s, max_n, sf, gf, log):
        channels, index = self._get_data(gf, sf, geo, quality, misc, log)
        os.makedirs("/data/exports", exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = "/data/exports/epg_suggester_" + ts + ".csv"
        with open(path, "w", newline="", encoding="utf-8") as fh:
            fh.write("# EPG Suggester | generated " + datetime.now().isoformat() + "\n")
            fh.write("# min_score=" + str(min_s) + " max_suggestions=" + str(max_n) + "\n#\n")
            w = csv.writer(fh)
            w.writerow(["channel_id", "channel_name", "channel_norm", "channel_group",
                        "rank", "score", "epg_name", "tvg_id", "epg_source", "epg_data_id"])
            for ch in channels:
                raw = ch["name"] or ""
                norm = _norm(raw, geo, quality, misc)
                sugg = self._suggest(norm, index, min_s, max_n)
                if sugg:
                    for rank, s in enumerate(sugg, 1):
                        w.writerow([ch["id"], raw, norm, ch.get("channel_group__name") or "",
                                    rank, s["score"], s["name"], s["tvg_id"], s["source"], s["id"]])
                else:
                    w.writerow([ch["id"], raw, norm, ch.get("channel_group__name") or "",
                                "", "", "NO_MATCH", "", "", ""])
        n = sum(1 for ch in channels if self._suggest(_norm(ch["name"] or "", geo, quality, misc), index, min_s, max_n))
        return "CSV exported to " + path + "\n" + str(len(channels)) + " channels, " + str(n) + " with suggestions."

    def _apply(self, geo, quality, misc, min_s, max_n, sf, gf, auto, thresh, log):
        if not auto:
            return "Auto-Apply is DISABLED. Enable it in settings first, then retry."
        from apps.channels.models import Channel
        channels, index = self._get_data(gf, sf, geo, quality, misc, log)
        applied = skipped = failed = 0
        lines = ["Auto-Apply (threshold=" + str(thresh) + ")", ""]
        for ch in channels:
            raw = ch["name"] or ""
            norm = _norm(raw, geo, quality, misc)
            sugg = self._suggest(norm, index, min_s, max_n)
            if not sugg:
                lines.append("SKIP (no match): " + raw)
                skipped += 1
                continue
            top = sugg[0]
            if top["score"] < thresh:
                lines.append("SKIP (score " + str(top["score"]) + " < " + str(thresh) + "): " + raw)
                skipped += 1
                continue
            try:
                Channel.objects.filter(pk=ch["id"]).update(epg_data_id=top["id"])
                lines.append("OK: " + raw + " -> " + top["name"] + " (score=" + str(top["score"]) + ")")
                applied += 1
            except Exception as e:
                lines.append("FAIL: " + raw + " -> " + str(e))
                failed += 1
        lines += ["", "Applied: " + str(applied), "Skipped: " + str(skipped), "Failed: " + str(failed)]
        return "\n".join(lines)
