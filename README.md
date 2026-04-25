# EPG Suggester — Dispatcharr Plugin

A Dispatcharr plugin that scans channels without EPG assignments and intelligently suggests the best matching EPG entry using fuzzy name matching and callsign detection.

[![Dispatcharr plugin](https://img.shields.io/badge/Dispatcharr-plugin-8A2BE2)](https://github.com/Dispatcharr/Dispatcharr)

---

## What It Does

When you have hundreds of IPTV channels with no EPG assigned, manually matching them one by one is painful. This plugin automates it.

It handles the messy reality of IPTV naming:

| Channel name in M3U | Matches EPG entry |
| --- | --- |
| `US\| CNN VIP HD` | `CNN` |
| `NO: SF-KANALEN ⱽᴵᴾ` | `NO: SF-KANALEN` |
| `PRIME: NBC ST. LOUIS NEWS (KSDK) ᴿᴬᵂ [FHD]` | `KSDK-DT` |
| `GO: ESPNEWS` | `ESPNEWS HD` |
| `SE: SVT BARN ᴴᴰ ⱽᴵᴾ` | `SE: SVT Barn ᴴᴰ` |

### Matching pipeline

1. **Strip noise** — Unicode superscript tags (`ᴴᴰ ⱽᴵᴾ ᴿᴬᵂ ᶠᴴᴰ`), quality tags (`HD`, `4K`, `UHD`), misc noise (`VIP`, `backup`, `+1`)
2. **Normalise prefixes** — country codes (`NO:`, `SE:`, `UK:`) are kept for country-aware matching; provider prefixes (`GO:`, `NOW:`, `VIP:`, `PRIME:`) are stripped
3. **Callsign matching** — channels containing a callsign like `(KSDK)` or `(WCAU)` are matched directly to EPG entries named `KSDK-DT`, `WCAU-DT` etc. at score 100
4. **Fuzzy scoring** — token overlap + SequenceMatcher ratio + substring bonus, with a number guard to prevent `History 2` matching `History 1`
5. **Country-indexed lookup** — `NO:` channels only search Norwegian EPG entries; cross-country false positives are eliminated
6. **Source priority** — when multiple sources match equally, your preferred sources win the tiebreak

---

## Installation

1. In Dispatcharr, go to **Settings → Plugins**
2. Click **Import Plugin** in the top right
3. Upload `epg_suggester.zip`
4. Click **Enable** on the plugin card
5. Configure settings and click **Save**

Or copy files directly into your Dispatcharr data directory:

```bash
mkdir -p /data/plugins/epg_suggester
cp plugin.py plugin.json /data/plugins/epg_suggester/
# Then reload plugins in the Dispatcharr UI
```

---

## Recommended Workflow

### 1 — Check your baseline

Click **📊 Statistics** for an instant overview of matched vs unmatched channels by group — no scan needed.

### 2 — Preview before committing

Click **📤 Export CSV** to save full results to `/data/exports/epg_suggester_TIMESTAMP.csv` without changing anything. Copy it out and inspect it:

```bash
docker cp dispatcharr:/data/exports/epg_suggester_TIMESTAMP.csv ./
```

Or click **🧪 Dry Run Apply** to see exactly which assignments would be written at your current threshold, directly in the UI.

### 3 — Understand the confidence tiers

- **Score 100** — callsign match or exact name match. Safe to apply without review.
- **Score 90–99** — high confidence fuzzy match. Quickly scan before applying.
- **Score 80–89** — moderate confidence. Manual review recommended.
- **Below 80** — treat as suggestions only; verify each one manually.

### 4 — Apply

**Option A — Auto-apply:** Set **Auto-Apply Min Score**, enable **Auto-Apply**, then click **✅ Apply Best Suggestions**. A rollback snapshot is always saved first.

**Option B — CSV workflow:** Export the CSV, delete any rows you don't want applied, then click **📥 Apply from CSV** to apply only what remains. Best for selective, reviewed application.

### 5 — If something goes wrong

Click **↩ Restore Last Apply** to revert all assignments to exactly what they were before the last apply operation.

### 6 — After adding a new EPG source

Click **🔎 Audit Matched Channels** to scan already-assigned channels and flag any where a better match now exists.

---

## Settings Reference

| Setting | Default | Description |
| --- | --- | --- |
| 🎯 Minimum Match Score | `60` | Threshold to include a suggestion (0–100). Lower = more suggestions but less accurate. |
| 📋 Max Suggestions Per Channel | `3` | Top-N results shown per channel in CSV and scan report (1–10). |
| 📡 Limit to EPG Sources | *(all)* | Comma-separated EPG source names to search. Leave blank for all. |
| ⭐ Preferred EPG Sources | *(none)* | When scores are equal, prefer sources listed here. Comma-separated, highest priority first. |
| 📂 Limit to Channel Groups | *(all)* | Comma-separated channel group names to scan. Leave blank for all. |
| 🌍 Strip Geographic Prefixes | `ON` | Removes `US\|`, `UK:` etc. before matching. Disable only if your channel names intentionally use country codes as part of the real name. |
| 🎬 Strip Quality Tags | `ON` | Removes `HD`, `4K`, `UHD` etc. before matching. |
| 🔧 Strip Misc Tags | `ON` | Removes `VIP`, `backup`, `+1` etc. before matching. |
| ⚡ Auto-Apply Best Match | `OFF` | When ON, Apply action writes EPG assignments. A rollback snapshot is always saved first. |
| 🔒 Auto-Apply Min Score | `85` | Safety floor for auto-apply, dry-run, and audit. Only matches at or above this score are acted on. |

---

## Actions

| Action | Description |
| --- | --- |
| 🔍 Scan & Suggest EPG | Runs the full matching engine and saves a text report to `/data/exports/`. Returns a preview in the UI. Each suggestion shows its score and match type (callsign / fuzzy). |
| 📤 Export Suggestions to CSV | Same as scan but saves results as a CSV for spreadsheet review. Edit the file, then use *Apply from CSV* to apply it. |
| 🧪 Dry Run Apply | Preview what *Apply Best Suggestions* would write, without touching the database. Uses the auto-apply threshold regardless of whether auto-apply is enabled. |
| ✅ Apply Best Suggestions | Assigns EPG to channels where the top suggestion meets the auto-apply threshold. Requires Auto-Apply to be enabled. Saves a rollback snapshot first. |
| 📥 Apply from CSV | Applies EPG assignments from the most recently exported CSV. Only rank=1 rows are processed — delete rows you don't want before running. Saves a rollback snapshot first. |
| ↩ Restore Last Apply | Reverts all EPG assignments to the state captured in the most recent rollback snapshot. Undoes both *Apply Best Suggestions* and *Apply from CSV*. |
| 🔎 Audit Matched Channels | Scans channels that already have EPG assigned and flags any where a better match now exists. Does not modify the database. Run after importing a new EPG source. |
| 📊 Statistics | Instant overview of matched / unmatched channel counts by group and total EPG entries available. No matching engine run. |
| 📺 List Unmatched Channels | Fast list of all channels with no EPG assigned, grouped by channel group. No scoring performed. |

---

## EPG Sources

The plugin works against whatever EPG sources you have loaded in Dispatcharr. More sources = better coverage.

**Recommended free sources:**

General international coverage:

```text
https://epg.pw/xmltv/epg_US.xml
https://epg.pw/xmltv/epg_GB.xml
```

US local affiliates (NBC/ABC/CBS/FOX) — includes callsign-based entries like `KSDK-DT`, `WCAU-DT`:

```text
https://epgshare01.online/epgshare01/epg_ripper_ALL_SOURCES1.xml.gz
```

---

## How Callsign Matching Works

Many IPTV providers include the broadcast callsign in parentheses in the channel name:

```text
PRIME: NBC ST. LOUIS NEWS (KSDK) ᴿᴬᵂ [FHD]
PRIME: CBS HARRISBURG (WHP) ᴿᴬᵂ [FHD]
US: FOX (KOKH) OKLAHOMA CITY HD
```

EPG sources like epgshare01 identify local stations by callsign:

```text
KSDK-DT   →  NBC St. Louis
WHP-DT    →  CBS Harrisburg
KOKH-DT   →  FOX Oklahoma City
```

The plugin extracts the callsign from the channel name and looks it up directly in a pre-built callsign index — bypassing fuzzy matching entirely and returning score 100. This makes US local affiliate matching reliable even when the channel name and EPG entry share no common words.

---

## Country Code Awareness

The plugin distinguishes between country code prefixes and provider prefixes:

**Country codes** (kept for country-scoped matching):
`US`, `UK`, `GB`, `NO`, `SE`, `DK`, `FI`, `DE`, `FR`, `IT`, `ES`, `NL`, `PL`, `RO`, `HU`, `TR`, `RU`, `AR`, `CL`, and many more.

**Provider prefixes** (stripped before matching):
`GO:`, `NOW:`, `VIP:`, `PRIME:`, `SKY:`, `NBA:`, `MLB:`, `DSTV:`, `VO:`, `MXC:`, `WOW:` etc.

This means `NO: SF-KANALEN` will never incorrectly match `FI: SF-KANALEN`, but `GO: CNN` will correctly match `US: CNN 4K` (the `GO:` provider prefix is stripped, then `CNN` matches across all countries).

Geo prefix stripping can be disabled in settings — useful if your channel names use country codes as part of the real name rather than as IPTV prefixes.

---

## Output Files

All output files are saved to `/data/exports/` inside the Dispatcharr container.

| File | Description |
| --- | --- |
| `epg_suggester_TIMESTAMP.csv` | Full suggestion results with scores, for review and *Apply from CSV* |
| `epg_suggester_scan_TIMESTAMP.txt` | Human-readable scan report |
| `epg_suggester_rollback_TIMESTAMP.json` | Pre-apply snapshot used by *Restore Last Apply* |

Copy a file out of the container:

```bash
docker cp dispatcharr:/data/exports/epg_suggester_TIMESTAMP.csv ./
```

### CSV columns

| Column | Description |
| --- | --- |
| `channel_id` | Dispatcharr internal channel ID |
| `channel_name` | Original channel name |
| `channel_norm` | Normalised name used for matching |
| `channel_group` | Channel group name |
| `rank` | Suggestion rank (1 = best) |
| `score` | Match confidence 0–100 |
| `match_type` | How the match was made: `callsign` or `fuzzy` |
| `epg_name` | Suggested EPG entry display name |
| `tvg_id` | EPG entry TVG ID |
| `epg_source` | EPG source name |
| `epg_data_id` | Dispatcharr internal EPG data ID |

---

## Performance

The plugin uses a word-inverted index and callsign index to avoid brute-force comparisons:

| EPG entries | Channels | Time |
| --- | --- | --- |
| ~4,000 | ~2,000 | ~5s |
| ~20,000 | ~2,000 | ~10s |
| ~46,000 | ~1,500 | ~10s |

---

## Troubleshooting

**"No suggestions above threshold"**
Lower the Minimum Match Score to 50 and re-run. If still nothing, the channel name shares no words with any EPG entry — the EPG source likely doesn't cover that channel.

**Cross-country false positives**
If a country code is missing from the plugin's known list, open an issue with the code and it will be added.

**504 Gateway Time-out**
The scan still completes and saves results to `/data/exports/` even if the HTTP response times out. Check there for the output file.

**Score 100 but wrong match**
This is a callsign collision — two different stations with the same callsign in different markets. Manually correct the assignment in Dispatcharr's Channels page.

**Applied wrong matches by mistake**
Click **↩ Restore Last Apply** to revert to the pre-apply state. Each apply operation saves a rollback snapshot automatically.

---

## License

MIT — free for personal and commercial use.

---

## Contributing

Pull requests welcome. Particularly useful contributions:

- Additional country codes for the prefix detection list
- Better handling of specific IPTV provider naming conventions
- Report false positives with channel name + EPG name so scoring can be improved
