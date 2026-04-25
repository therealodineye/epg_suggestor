import re as _re
import logging
import difflib

# ---------------------------------------------------------------------------
# Module-level compiled patterns — compiled once at import time for speed.
# ---------------------------------------------------------------------------

# Detects a normalised country prefix in the form "xx: " (e.g. "us: cnn")
_CTRY_RE     = _re.compile(r'^([a-z]{2,5}): ')
# Detects raw IPTV-style prefixes before the real channel name (e.g. "US| ", "UK: ", "FR-")
_PREFIX_RE   = _re.compile(r'^([A-Za-z]{2,5})\s*[|\-:]\s*')
# Lookalike Unicode letters (superscript, small-caps, etc.) that should map to spaces
_UNICODE_RE  = _re.compile(r'[ᴀ-ᶿⱠ-Ɀ⁰-₟²-³¹]+')
# Quality / resolution tags that add no matching value
_QUALITY_RE  = _re.compile(r'\b(?:4k|uhd|fhd|hd|sd|hevc|h265|h264|hdr|sdr|1080[pi]?|720[pi]?)\b', _re.IGNORECASE)
# Misc IPTV noise: tier labels, backup copies, parenthetical/bracketed suffixes, asterisks
_MISC_RE     = _re.compile(r'\b(?:vip|backup\d*|bkup|plus|premium|extra|alt|raw|\+1|\+2)\b|\([^)]{0,15}\)|\[[^\]]{0,15}\]|\s*\*+\s*', _re.IGNORECASE)
# Collapses any run of whitespace to a single space
_WS_RE       = _re.compile(r'\s+')
# Extracts a broadcast callsign embedded in channel names e.g. "(KSDK)" or "(KSDK-DT)"
_CALLSIGN_RE = _re.compile(r'\(([A-Z]{2,5}(?:-[A-Z0-9]+)?)\)')

# All known two-to-five letter ISO / regional codes used as IPTV channel prefixes
_COUNTRY_CODES = {
    'us','uk','gb','au','ca','de','fr','it','es','nl','be','ch','at',
    'no','se','dk','fi','pl','pt','ro','al','sr','hr','si','sk','cz',
    'hu','rs','ba','me','mk','bg','gr','tr','il','ar','br','mx','nz',
    'za','ie','is','lu','ee','lv','lt','ua','by','md','ge','am','az',
    'kz','uz','pk','in','sg','my','th','ph','id','jp','kr','cn','hk',
    'tw','ae','sa','qa','kw','bh','om','eg','ma','tn','dz','ly','ng',
    'ke','gh','tz','et','cm','ci','sn','rw','ug','ao','mz','ru','cl',
}

# Very common short words excluded from the word index — matching on them produces too many
# irrelevant candidates and slows down scoring.
_STOP_WORDS = {'hd','sd','tv','the','and','for','live','news','channel','network'}

# Single place to change the output directory used by all export/snapshot operations
_EXPORT_DIR = "/data/exports"


class Plugin:
    name        = "EPG Suggester"
    version     = "2.3.0"
    description = "Suggests EPG entries for channels without EPG assigned, using fuzzy name matching."

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def run(self, action, params, context):
        """
        Entry point called by Dispatcharr for every plugin action.
        Parses settings, resolves the action name to a method, and returns
        either a string (displayed in the Dispatcharr UI) or an error dict.
        """
        log      = logging.getLogger("plugins.epg_suggester")
        settings = context.get("settings", {})
        cfg      = self._parse_settings(settings)
        log.info("EPG Suggester: action=%s", action)

        actions = {
            "show_unmatched":         self._show_unmatched,
            "scan_and_suggest":       self._scan,
            "export_suggestions_csv": self._export,
            "apply_suggestions":      self._apply,
            "dry_run_apply":          self._dry_run_apply,
            "restore_last_apply":     self._restore_last_apply,
            "audit_matched":          self._audit_matched,
            "show_stats":             self._show_stats,
            "apply_from_csv":         self._apply_from_csv,
        }
        fn = actions.get(action)
        if fn:
            return fn(cfg, log)
        return {"status": "error", "message": "Unknown action: " + action}

    # ------------------------------------------------------------------
    # Settings
    # ------------------------------------------------------------------

    def _parse_settings(self, settings):
        """
        Convert the raw settings dict supplied by Dispatcharr into typed config values.
        Numeric fields are clamped to valid ranges so downstream code never needs to
        guard against out-of-range values.  Invalid (non-numeric) values fall back to
        the built-in defaults rather than raising an exception.
        """
        def _int(key, default, lo, hi):
            try:
                return max(lo, min(hi, int(settings.get(key, default))))
            except (ValueError, TypeError):
                return default

        return {
            "geo":    bool(settings.get("ignore_geo_prefixes", True)),
            "qual":   bool(settings.get("ignore_quality_tags", True)),
            "misc":   bool(settings.get("ignore_misc_tags", True)),
            "min_s":  _int("min_score", 60, 0, 100),
            "max_n":  _int("max_suggestions", 3, 1, 10),
            "thresh": _int("auto_apply_threshold", 85, 0, 100),
            "sf":     [x.strip() for x in settings.get("epg_sources_filter", "").split(",") if x.strip()],
            "gf":     [x.strip() for x in settings.get("group_filter", "").split(",") if x.strip()],
            "auto":   bool(settings.get("auto_apply", False)),
            "prio":   [x.strip() for x in settings.get("preferred_sources", "").split(",") if x.strip()],
        }

    # ------------------------------------------------------------------
    # Name normalisation
    # ------------------------------------------------------------------

    @staticmethod
    def _norm(name, cfg):
        """
        Normalise a raw channel or EPG display name into a clean lowercase string
        suitable for comparison.

        Steps applied in order:
          1. Strip leading/trailing whitespace.
          2. Replace lookalike Unicode characters (small-caps, superscripts) with spaces.
          3. If geo=True: detect IPTV country prefixes (e.g. "US| ", "UK: ").
             Known country codes are kept as "xx: name" so the country-bucket index works;
             unrecognised prefixes (e.g. "PRIME|") are stripped entirely.
          4. If qual=True: remove quality/resolution tags (HD, 4K, FHD, …).
          5. If misc=True: remove IPTV noise words (VIP, backup, +1, …) and
             short parenthetical / bracketed suffixes.
          6. Collapse all whitespace runs to a single space.
        """
        n = name.strip()
        n = _UNICODE_RE.sub(' ', n)
        if cfg["geo"]:
            m = _PREFIX_RE.match(n)
            if m:
                prefix = m.group(1).lower()
                rest   = n[m.end():]
                n = (prefix + ': ' + rest) if prefix in _COUNTRY_CODES else rest
        if cfg["qual"]: n = _QUALITY_RE.sub(' ', n)
        if cfg["misc"]: n = _MISC_RE.sub(' ', n)
        return _WS_RE.sub(' ', n).strip().lower()

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    @staticmethod
    def _fast_score(ct, cs, cn, et, es, en, min_s):
        """
        Compute a similarity score (0-99) between a channel and an EPG entry.

        Uses a tiered heuristic to avoid calling the slower SequenceMatcher for
        candidates that clearly won't score high enough:

          1. Exact normalised match → 100 (early exit).
          2. Number clash: if both names contain digits that share no common value → 0
             (e.g. "BBC 1" should never match "BBC 2").
          3. Word-overlap score: (shared words / max word count) × 90.
          4. Substring bonus: +20 if one name is contained in the other.
          5. Early exit if overlap + bonus < min_s (skip SequenceMatcher).
          6. SequenceMatcher ratio (sorted tokens) used to refine the score when the
             word overlap looks promising (≥ 40) or a substring relationship exists.

        Returns an int in range [0, 99].  Exact matches (100) are handled by the
        caller before this function is invoked.
        """
        if cn == en: return 100
        ch_nums = set(t for t in ct if t.isdigit())
        ep_nums = set(t for t in et if t.isdigit())
        if ch_nums and ep_nums and not (ch_nums & ep_nums):
            return 0
        inter     = len(cs & es) if cs and es else 0
        union     = max(len(cs), len(es)) if (cs or es) else 1
        overlap_s = int(inter / union * 90)
        sub       = 20 if (cn in en or en in cn) else 0
        if overlap_s + sub < min_s:
            return overlap_s + sub
        if overlap_s >= 40 or sub:
            ratio     = difflib.SequenceMatcher(None, ' '.join(sorted(ct)), ' '.join(sorted(et))).ratio()
            overlap_s = max(overlap_s, int(ratio * 90))
        return min(99, overlap_s + sub)

    # ------------------------------------------------------------------
    # Index construction
    # ------------------------------------------------------------------

    def _build_index(self, epg_entries, cfg):
        """
        Build fast lookup structures from the full list of EPG entries.

        Returns a 4-tuple:
          by_country (dict)      — country_code → [entry, …]
                                   Entries whose normalised name begins with "xx: ".
          no_country (list)      — Entries with no country prefix.
          word_index (dict)      — word → [entry, …]
                                   Only covers no_country entries; used to narrow the
                                   candidate set without scanning every EPG entry.
          callsign_index (dict)  — callsign → [entry, …]
                                   Maps leading callsigns (e.g. "KSDK") to all entries
                                   whose raw name starts with that callsign, enabling
                                   O(1) callsign lookups.
        """
        by_country = {}
        no_country = []
        word_index = {}

        for e in epg_entries:
            raw = (e["name"] or "").strip()
            if not raw:
                continue
            norm = self._norm(raw, cfg)
            tok  = norm.split()
            tset = set(tok)
            # Extract the leading callsign from the raw EPG name.
            # "KSDK-DT HD" → raw_upper "KSDK-DT HD" → cs_match group 1 "KSDK"
            raw_upper    = raw.strip().upper()
            cs_match     = _re.match(r'^([A-Z]{2,5})(?:[-.]|$)', raw_upper)
            epg_callsign = cs_match.group(1) if cs_match and _re.match(r'^[A-Z]{2,5}$', cs_match.group(1)) else ''
            entry = {
                "id":           e["id"],
                "name":         raw,
                "tvg_id":       e["tvg_id"] or "",
                "source":       e["epg_source__name"] or "",
                "norm":         norm,
                "tok":          tok,
                "tset":         tset,
                "epg_callsign": epg_callsign,
            }
            m = _CTRY_RE.match(norm)
            if m:
                by_country.setdefault(m.group(1), []).append(entry)
            else:
                no_country.append(entry)
                for word in tset:
                    if len(word) >= 3 and word not in _STOP_WORDS:
                        word_index.setdefault(word, []).append(entry)

        # Build callsign index across all entries (country and no-country buckets)
        callsign_index = {}
        for entries in [no_country] + list(by_country.values()):
            for entry in entries:
                cs = entry.get("epg_callsign", "")
                if cs:
                    callsign_index.setdefault(cs, []).append(entry)

        return by_country, no_country, word_index, callsign_index

    # ------------------------------------------------------------------
    # Candidate retrieval
    # ------------------------------------------------------------------

    def _candidates_for(self, ch_norm, ch_tok, ch_set, by_country, no_country, word_index):
        """
        Return a deduplicated list of EPG entries worth scoring against this channel.

        Strategy:
          - Country-prefixed channel ("us: cnn"): pull only the matching country bucket.
          - No-country entries: use word_index to find entries sharing at least one
            meaningful word — avoids scanning all no-country entries every time.
          - If the channel has no country prefix: also search country buckets via word
            overlap, so "CNN" can still match "us: cnn".
          - Falls back to a full no_country scan only when no meaningful words exist
            (e.g. single-letter names or names composed entirely of stop words/digits).
        """
        m = _CTRY_RE.match(ch_norm)
        country_entries = by_country.get(m.group(1), []) if m else []

        meaningful = [w for w in ch_tok if len(w) >= 3 and w not in _STOP_WORDS and not w.isdigit()]
        if meaningful:
            seen         = set()
            nc_candidates = []
            for word in meaningful:
                for entry in word_index.get(word, []):
                    eid = entry["id"]
                    if eid not in seen:
                        seen.add(eid)
                        nc_candidates.append(entry)
        else:
            nc_candidates = no_country

        if not m:
            seen_c = set()
            for word in meaningful:
                for country_list in by_country.values():
                    for entry in country_list:
                        if word in entry["tset"] and entry["id"] not in seen_c:
                            seen_c.add(entry["id"])
                            nc_candidates.append(entry)

        return country_entries + nc_candidates

    # ------------------------------------------------------------------
    # Suggestion engine
    # ------------------------------------------------------------------

    def _suggest(self, ch_norm, ch_raw, by_country, no_country, word_index, callsign_index, cfg):
        """
        Score all candidates for a single channel and return the top-N matches.

        Callsign matches (e.g. "(KSDK)" found in the raw channel name) always score
        100 and are added directly from callsign_index.

        Remaining candidates are scored via _fast_score.  Results are sorted by score
        descending, then by source priority ascending (lower index in cfg['prio'] list
        = higher priority), so preferred sources win ties.

        Each returned dict is the EPG entry enriched with:
          score      — int, 0-100
          match_type — "callsign" or "fuzzy"
        """
        ct    = ch_norm.split()
        cs    = set(ct)
        min_s = cfg["min_s"]

        # Extract callsign embedded in the raw channel name, e.g. "(KSDK)" or "(KSDK-DT)"
        ch_callsign = ''
        cm = _CALLSIGN_RE.search(ch_raw)
        if cm:
            ch_callsign = cm.group(1).upper()
            ch_callsign = _re.sub(r'[-.].*$', '', ch_callsign)

        candidates = self._candidates_for(ch_norm, ct, cs, by_country, no_country, word_index)
        if ch_callsign:
            candidates = candidates + callsign_index.get(ch_callsign, [])

        prio_map   = {s: i for i, s in enumerate(cfg["prio"])} if cfg["prio"] else {}
        prio_worst = len(cfg["prio"])
        scored     = []
        seen_ids   = set()

        for e in candidates:
            eid = e["id"]
            if eid in seen_ids:
                continue
            seen_ids.add(eid)
            if ch_callsign and e.get("epg_callsign") == ch_callsign:
                scored.append((100, prio_map.get(e["source"], prio_worst), e, "callsign"))
            else:
                s = self._fast_score(ct, cs, ch_norm, e["tok"], e["tset"], e["norm"], min_s)
                if s >= min_s:
                    scored.append((s, prio_map.get(e["source"], prio_worst), e, "fuzzy"))

        # Primary sort: score descending. Tiebreaker: source priority ascending.
        scored.sort(key=lambda x: (-x[0], x[1]))

        result = []
        for s, _, e, match_type in scored:
            result.append(dict(e, score=s, match_type=match_type))
            if len(result) >= cfg["max_n"]:
                break
        return result

    # ------------------------------------------------------------------
    # Database helpers
    # ------------------------------------------------------------------

    def _get_channels(self, cfg, log, matched=False, order_by=None):
        """
        Fetch channels from the database.

        matched=False (default) — channels with no EPG assigned (epg_data__isnull=True).
        matched=True            — channels that already have EPG assigned; the returned
                                  dicts include 'epg_data_id' and 'epg_data__name' for
                                  use by the audit action.
        order_by                — optional list of ORM field names passed to .order_by().
        Respects cfg['gf'] group filter in both modes.
        """
        from apps.channels.models import Channel
        qs = Channel.objects.select_related("channel_group").filter(
            epg_data__isnull=(not matched)
        )
        if cfg["gf"]:
            qs = qs.filter(channel_group__name__in=cfg["gf"])
        if order_by:
            qs = qs.order_by(*order_by)
        if matched:
            channels = list(qs.values("id", "name", "channel_group__name", "epg_data_id", "epg_data__name"))
        else:
            channels = list(qs.values("id", "name", "channel_group__name"))
        log.info("EPG Suggester: %d channels fetched (matched=%s)", len(channels), matched)
        return channels

    def _get_epg(self, cfg, log):
        """
        Fetch EPG entries from the database, optionally restricted to specific sources
        via cfg['sf'].  Returns a flat list of dicts with id, name, tvg_id, and source name.
        """
        from apps.epg.models import EPGData
        qs = EPGData.objects.select_related("epg_source").values(
            "id", "name", "tvg_id", "epg_source__name"
        )
        if cfg["sf"]:
            qs = qs.filter(epg_source__name__in=cfg["sf"])
        entries = list(qs)
        log.info("EPG Suggester: %d EPG entries fetched", len(entries))
        return entries

    # ------------------------------------------------------------------
    # Core pipeline
    # ------------------------------------------------------------------

    def _run_matching(self, cfg, log):
        """
        Full matching pipeline: fetch unmatched channels + EPG data, build the index,
        then run _suggest for every channel.

        Returns a list of result dicts — one per unmatched channel:
          channel_id, channel_name, channel_norm, channel_group, suggestions (list).
        Each suggestion is an EPG entry dict augmented with 'score' and 'match_type'.
        """
        channels                                            = self._get_channels(cfg, log)
        epg_raw                                             = self._get_epg(cfg, log)
        by_country, no_country, word_index, callsign_index = self._build_index(epg_raw, cfg)
        log.info(
            "EPG Suggester: index built (%d country groups, %d no-prefix, %d word-index keys, %d callsigns)",
            len(by_country), len(no_country), len(word_index), len(callsign_index),
        )
        results = []
        for ch in channels:
            raw  = ch["name"] or ""
            norm = self._norm(raw, cfg)
            sugg = self._suggest(norm, raw, by_country, no_country, word_index, callsign_index, cfg)
            results.append({
                "channel_id":    ch["id"],
                "channel_name":  raw,
                "channel_norm":  norm,
                "channel_group": ch.get("channel_group__name") or "",
                "suggestions":   sugg,
            })
        matched = sum(1 for r in results if r["suggestions"])
        log.info("EPG Suggester: matching done. %d/%d channels matched", matched, len(results))
        return results

    # ------------------------------------------------------------------
    # Rollback helper (shared by _apply and _apply_from_csv)
    # ------------------------------------------------------------------

    def _save_rollback(self, channel_ids, log):
        """
        Snapshot the current epg_data_id values for the given channel IDs.
        Written as JSON to _EXPORT_DIR/epg_suggester_rollback_TIMESTAMP.json before
        any database writes so the operation can be fully reversed.
        Returns the path to the snapshot file.
        """
        import json, os
        from datetime import datetime
        from apps.channels.models import Channel
        snapshot = list(Channel.objects.filter(pk__in=channel_ids).values("id", "epg_data_id"))
        os.makedirs(_EXPORT_DIR, exist_ok=True)
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = _EXPORT_DIR + "/epg_suggester_rollback_" + ts + ".json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(snapshot, f)
        log.info("EPG Suggester: rollback snapshot -> %s (%d channels)", path, len(snapshot))
        return path

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _show_unmatched(self, cfg, log):
        """
        List all channels that currently have no EPG assigned, grouped by channel group.
        Does not run the suggestion engine — this is a fast overview action.
        """
        channels = self._get_channels(cfg, log, order_by=["channel_group__name", "name"])
        if not channels:
            return "All channels already have EPG assigned!"
        lines = [str(len(channels)) + " channels without EPG:\n"]
        grp   = None
        for c in channels:
            g = c.get("channel_group__name") or "No Group"
            if g != grp:
                lines.append("\n[" + g + "]")
                grp = g
            lines.append("  id=" + str(c["id"]) + "  " + (c["name"] or ""))
        return "\n".join(lines)

    def _scan(self, cfg, log):
        """
        Run the full matching pipeline and write a human-readable report to _EXPORT_DIR.
        Shows the first 60 lines as an in-UI preview; the full path is included so the
        report can be retrieved with `docker cp`.
        Each suggestion line includes its match_type (callsign / fuzzy) for transparency.
        """
        import os
        from datetime import datetime
        results = self._run_matching(cfg, log)
        matched = sum(1 for r in results if r["suggestions"])
        lines   = [
            "EPG Suggester v" + self.version + " - Scan Results",
            str(len(results)) + " unmatched  |  suggestions found: " + str(matched),
            "",
        ]
        for r in results:
            lines.append("---")
            lines.append("Channel: " + r["channel_name"] + "  [" + r["channel_group"] + "]")
            lines.append("  norm: " + r["channel_norm"])
            if r["suggestions"]:
                for i, s in enumerate(r["suggestions"], 1):
                    lines.append(
                        "  [" + str(i) + "] score=" + str(s["score"])
                        + " (" + s.get("match_type", "fuzzy") + ")"
                        + "  " + s["name"]
                        + "  source=" + s["source"]
                        + "  id=" + str(s["id"])
                    )
            else:
                lines.append("  No suggestions above score " + str(cfg["min_s"]))
        os.makedirs(_EXPORT_DIR, exist_ok=True)
        ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = _EXPORT_DIR + "/epg_suggester_scan_" + ts + ".txt"
        with open(out, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        log.info("EPG Suggester: scan saved to %s", out)
        preview = "\n".join(lines[:60])
        if len(lines) > 60:
            preview += "\n\n... full results in " + out
        return preview

    def _export(self, cfg, log):
        """
        Run the matching pipeline and save all results to a timestamped CSV in _EXPORT_DIR.
        Channels with no suggestions are written as NO_MATCH rows so they are visible.
        The CSV includes a 'match_type' column (callsign / fuzzy) alongside the score.
        Returns the file path and a ready-to-run `docker cp` command.
        """
        import csv, os
        from datetime import datetime
        results = self._run_matching(cfg, log)
        matched = sum(1 for r in results if r["suggestions"])
        os.makedirs(_EXPORT_DIR, exist_ok=True)
        ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = _EXPORT_DIR + "/epg_suggester_" + ts + ".csv"
        with open(path, "w", newline="", encoding="utf-8") as fh:
            fh.write("# EPG Suggester v" + self.version + " | " + datetime.now().isoformat() + "\n")
            fh.write("# min_score=" + str(cfg["min_s"]) + "  max_suggestions=" + str(cfg["max_n"]) + "\n#\n")
            w = csv.writer(fh)
            w.writerow(["channel_id", "channel_name", "channel_norm", "channel_group",
                        "rank", "score", "match_type", "epg_name", "tvg_id", "epg_source", "epg_data_id"])
            for r in results:
                if r["suggestions"]:
                    for rank, s in enumerate(r["suggestions"], 1):
                        w.writerow([
                            r["channel_id"], r["channel_name"], r["channel_norm"], r["channel_group"],
                            rank, s["score"], s.get("match_type", "fuzzy"),
                            s["name"], s["tvg_id"], s["source"], s["id"],
                        ])
                else:
                    w.writerow([r["channel_id"], r["channel_name"], r["channel_norm"],
                                r["channel_group"], "", "", "", "NO_MATCH", "", "", ""])
        log.info("EPG Suggester: CSV saved to %s  (%d/%d matched)", path, matched, len(results))
        return (
            "CSV saved to " + path
            + "\nMatched: " + str(matched) + " / " + str(len(results))
            + "\n\ndocker cp dispatcharr:" + path + " ./"
        )

    def _apply(self, cfg, log):
        """
        Write EPG assignments to the database for every unmatched channel whose top
        suggestion meets or exceeds the auto-apply threshold (cfg['thresh']).

        Safety gates:
          - Requires cfg['auto'] (Auto-Apply setting) to be enabled.
          - Saves a rollback snapshot before any writes so the operation can be
            reversed with the 'restore_last_apply' action.

        Returns a summary of applied / skipped / failed counts plus the rollback path.
        """
        from apps.channels.models import Channel
        if not cfg["auto"]:
            return "Auto-Apply is DISABLED. Enable it in settings first."
        results  = self._run_matching(cfg, log)
        to_apply = [
            r for r in results
            if r["suggestions"] and r["suggestions"][0]["score"] >= cfg["thresh"]
        ]
        if not to_apply:
            return "No suggestions met the threshold of " + str(cfg["thresh"]) + ". Nothing applied."

        rollback_path = self._save_rollback([r["channel_id"] for r in to_apply], log)
        applied = failed = 0
        skipped = len(results) - len(to_apply)

        for r in to_apply:
            top = r["suggestions"][0]
            try:
                Channel.objects.filter(pk=r["channel_id"]).update(epg_data_id=top["id"])
                log.info("EPG Suggester: APPLY  %s -> %s (score=%d)",
                         r["channel_name"], top["name"], top["score"])
                applied += 1
            except Exception as e:
                log.error("EPG Suggester: FAIL  %s -> %s", r["channel_name"], e)
                failed += 1

        return (
            "Applied: " + str(applied)
            + "  Skipped: " + str(skipped)
            + "  Failed: " + str(failed)
            + "\nRollback saved to: " + rollback_path
        )

    def _dry_run_apply(self, cfg, log):
        """
        Preview exactly what 'apply_suggestions' would write to the database, without
        making any changes.  Uses cfg['thresh'] as the threshold regardless of whether
        auto_apply is currently enabled, so you can safely evaluate before turning it on.
        Useful for tuning the threshold before committing.
        """
        results     = self._run_matching(cfg, log)
        would_apply = [
            r for r in results
            if r["suggestions"] and r["suggestions"][0]["score"] >= cfg["thresh"]
        ]
        would_skip  = len(results) - len(would_apply)
        lines = [
            "EPG Suggester v" + self.version + " - Dry Run (no changes written)",
            "Threshold: " + str(cfg["thresh"])
            + "  |  Would apply: " + str(len(would_apply))
            + "  |  Would skip: " + str(would_skip),
            "",
        ]
        for r in would_apply:
            top = r["suggestions"][0]
            lines.append("  " + r["channel_name"] + "  [" + r["channel_group"] + "]")
            lines.append(
                "    -> " + top["name"]
                + "  (score=" + str(top["score"])
                + ", " + top.get("match_type", "fuzzy")
                + ", source=" + top["source"] + ")"
            )
        return "\n".join(lines)

    def _restore_last_apply(self, cfg, log):
        """
        Revert EPG assignments to the state captured by the most recent rollback snapshot.
        Reads the latest epg_suggester_rollback_*.json file from _EXPORT_DIR and writes
        the saved epg_data_id values back to the database.  Channels that were unmatched
        before the apply will have their epg_data_id set back to NULL.
        """
        import json, glob, os
        from apps.channels.models import Channel
        files = sorted(glob.glob(_EXPORT_DIR + "/epg_suggester_rollback_*.json"), reverse=True)
        if not files:
            return "No rollback snapshot found in " + _EXPORT_DIR + "."
        latest = files[0]
        with open(latest, "r", encoding="utf-8") as f:
            snapshot = json.load(f)
        restored = failed = 0
        for entry in snapshot:
            try:
                Channel.objects.filter(pk=entry["id"]).update(epg_data_id=entry["epg_data_id"])
                restored += 1
            except Exception as e:
                log.error("EPG Suggester: RESTORE FAIL  channel_id=%s -> %s", entry["id"], e)
                failed += 1
        log.info("EPG Suggester: restored %d channels from %s", restored, os.path.basename(latest))
        return (
            "Restored " + str(restored) + " channels from " + os.path.basename(latest)
            + ".  Failed: " + str(failed)
        )

    def _audit_matched(self, cfg, log):
        """
        Scan channels that already have EPG assigned and flag any where a better match
        exists in the current EPG data.

        'Better' is defined as: the top suggestion is a *different* EPG entry and its
        score meets cfg['thresh'].  This catches stale assignments after a new EPG source
        is added or after the EPG source content changes significantly.

        Does not modify the database — review the output and use 'apply_suggestions' or
        'apply_from_csv' to act on the findings.
        """
        channels                                            = self._get_channels(cfg, log, matched=True)
        epg_raw                                             = self._get_epg(cfg, log)
        by_country, no_country, word_index, callsign_index = self._build_index(epg_raw, cfg)
        flagged = []
        for ch in channels:
            raw  = ch["name"] or ""
            norm = self._norm(raw, cfg)
            sugg = self._suggest(norm, raw, by_country, no_country, word_index, callsign_index, cfg)
            if sugg:
                top = sugg[0]
                if top["id"] != ch["epg_data_id"] and top["score"] >= cfg["thresh"]:
                    flagged.append({
                        "channel":    raw,
                        "group":      ch.get("channel_group__name") or "No Group",
                        "current":    ch.get("epg_data__name") or ("id=" + str(ch["epg_data_id"])),
                        "suggested":  top["name"],
                        "score":      top["score"],
                        "source":     top["source"],
                        "match_type": top.get("match_type", "fuzzy"),
                    })
        if not flagged:
            return "No better matches found for already-assigned channels."
        lines = [str(len(flagged)) + " channels may have a better EPG match:\n"]
        for fl in flagged:
            lines.append("  " + fl["channel"] + "  [" + fl["group"] + "]")
            lines.append("    Current:   " + fl["current"])
            lines.append(
                "    Suggested: " + fl["suggested"]
                + "  (score=" + str(fl["score"])
                + ", " + fl["match_type"]
                + ", source=" + fl["source"] + ")"
            )
            lines.append("")
        return "\n".join(lines)

    def _show_stats(self, cfg, log):
        """
        Return a quick statistics overview without running the matching engine.
        Shows total / matched / unmatched channel counts broken down by channel group,
        and the total number of EPG entries available across all (or filtered) sources.
        Useful as a lightweight health-check before deciding whether a full scan is needed.
        """
        from apps.channels.models import Channel
        from apps.epg.models import EPGData
        from django.db.models import Count
        total     = Channel.objects.count()
        matched   = Channel.objects.filter(epg_data__isnull=False).count()
        unmatched = total - matched
        epg_total = EPGData.objects.count()
        groups    = (Channel.objects
                     .values("channel_group__name")
                     .annotate(total=Count("id"), matched=Count("epg_data"))
                     .order_by("channel_group__name"))
        lines = [
            "EPG Suggester v" + self.version + " - Statistics",
            "",
            "Channels  : " + str(total) + " total  |  " + str(matched) + " matched  |  " + str(unmatched) + " unmatched",
            "EPG Entries: " + str(epg_total),
            "",
            "  {:<30} {:>6} {:>8} {:>10}".format("Group", "Total", "Matched", "Unmatched"),
            "  " + "-" * 56,
        ]
        for g in groups:
            name = (g["channel_group__name"] or "No Group")[:30]
            t    = g["total"]
            m    = g["matched"]
            lines.append("  {:<30} {:>6} {:>8} {:>10}".format(name, t, m, t - m))
        return "\n".join(lines)

    def _apply_from_csv(self, cfg, log):
        """
        Apply EPG assignments from the most recently exported CSV file.

        Workflow:
          1. Finds the latest epg_suggester_TIMESTAMP.csv in _EXPORT_DIR.
          2. Reads all rank=1 rows (skipping NO_MATCH and comment lines).
          3. Saves a rollback snapshot before writing.
          4. Writes epg_data_id to the database for each row.

        To control which assignments are made, edit the CSV before running this action:
          - Delete any rows you do not want applied.
          - Only rank=1 rows are processed; other ranks are ignored.
        This gives you a review-then-apply workflow without needing to re-run the scan.
        """
        import csv, glob, os
        from apps.channels.models import Channel
        # Match only timestamped export files (YYYYMMDD_...), not scan txt files or rollbacks
        files = sorted(glob.glob(_EXPORT_DIR + "/epg_suggester_[0-9]*.csv"), reverse=True)
        if not files:
            return "No EPG Suggester CSV export found in " + _EXPORT_DIR + "."
        latest   = files[0]
        to_apply = []
        with open(latest, "r", encoding="utf-8") as fh:
            rows = [line for line in fh if not line.startswith("#")]
        for row in csv.DictReader(rows):
            if row.get("epg_name") == "NO_MATCH":
                continue
            try:
                if int(row.get("rank") or 0) != 1:
                    continue
                to_apply.append({
                    "channel_id":   int(row["channel_id"]),
                    "epg_data_id":  int(row["epg_data_id"]),
                    "channel_name": row.get("channel_name", ""),
                    "epg_name":     row.get("epg_name", ""),
                })
            except (KeyError, ValueError):
                continue
        if not to_apply:
            return "No applicable rank=1 rows found in " + os.path.basename(latest) + "."
        rollback_path = self._save_rollback([r["channel_id"] for r in to_apply], log)
        applied = failed = 0
        for r in to_apply:
            try:
                Channel.objects.filter(pk=r["channel_id"]).update(epg_data_id=r["epg_data_id"])
                log.info("EPG Suggester: CSV APPLY  %s -> %s", r["channel_name"], r["epg_name"])
                applied += 1
            except Exception as e:
                log.error("EPG Suggester: CSV APPLY FAIL  %s -> %s", r["channel_name"], e)
                failed += 1
        return (
            "Applied " + str(applied) + " assignments from " + os.path.basename(latest)
            + ".  Failed: " + str(failed)
            + "\nRollback saved to: " + rollback_path
        )
