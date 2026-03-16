# EPG Suggester — Dispatcharr Plugin

> Scans channels with no EPG assigned and **suggests the best matching EPG entry** using intelligent fuzzy name matching.

---

## What It Does

When you have IPTV channels with names like `US| CNN VIP HD` but your EPG source has an entry called `CNN`, this plugin bridges that gap.

**Matching pipeline:**
1. **Normalise** the channel name — strip geographic prefixes (`US|`, `UK:`, …), quality tags (`HD`, `4K`, `UHD`…), and common IPTV noise words (`vip`, `backup`, `+1`…)
2. **Score** every EPGData entry using a multi-strategy scorer:
   - Exact match after normalisation → 100 pts
   - Token-set ratio (difflib SequenceMatcher) → up to 90 pts
   - Word-overlap ratio → up to 60 pts
   - Contains / substring bonus → up to 20 pts
3. Return the **top-N suggestions** above your configured threshold

---

## Installation

1. Copy the `epg_suggester/` folder into your Dispatcharr data directory:
   ```
   data/plugins/epg_suggester/
   ├── plugin.json
   ├── plugin.py
   └── README.md
   ```
   Or use **Import Plugin** (zip the folder first) in the Dispatcharr UI.

2. In the Dispatcharr web UI go to **Settings → Plugins**.
3. Find **EPG Suggester** and click **Enable**.
4. Enter your **Dispatcharr URL**, **Admin Username**, and **Admin Password**.
5. Click **Save Settings**.

---

## Recommended Workflow

### 1 — Preview first (always)
Click **📤 Export CSV** — this runs the suggestion engine and saves results to:
```
/data/exports/epg_suggester_YYYYMMDD_HHMMSS.csv
```
Open the CSV and inspect the `score` column and `epg_name` column to verify the suggestions look correct.

### 2 — Run a visual scan
Click **🔍 Scan & Suggest EPG** to see a formatted text report directly in the Dispatcharr plugin UI.

### 3 — Apply automatically (optional)
- Set **⚡ Auto-Apply Best Match** to `ON`
- Set **🔒 Auto-Apply Min Score Override** to at least `85` (recommended `90+`)
- Click **✅ Apply Best Suggestions**

Only channels whose top suggestion meets or exceeds the auto-apply threshold will be updated. All others are skipped safely.

---

## Settings Reference

| Setting | Default | Description |
|---|---|---|
| 🌐 Dispatcharr URL | `http://127.0.0.1:9191` | Full URL to your instance |
| 👤 Admin Username | `admin` | Dispatcharr admin user |
| 🔑 Admin Password | *(empty)* | Required — stored securely |
| 🎯 Minimum Match Score | `50` | Threshold to include a suggestion (0–100) |
| 📋 Max Suggestions Per Channel | `3` | Top-N results shown per channel |
| 📡 Limit to EPG Sources | *(all)* | Comma-separated EPG source names to search |
| 📂 Limit to Channel Groups | *(all)* | Comma-separated group names to scan |
| 🎬 Strip Quality Tags | `ON` | Removes HD, 4K, UHD… before matching |
| 🌍 Strip Geo Prefixes | `ON` | Removes US\|, UK:, USA: before matching |
| 🔧 Strip Misc Tags | `ON` | Removes vip, backup, +1… before matching |
| ⚡ Auto-Apply Best Match | `OFF` | When ON, apply top match automatically |
| 🔒 Auto-Apply Min Score Override | `85` | Safety floor for auto-apply |

---

## Example

Channel name: `US| CNN VIP HD`

After normalisation (strip geo + quality + misc):
→ `cnn`

EPG entries scored:
| EPG Name | Normalised | Score |
|---|---|---|
| CNN | cnn | **100** |
| CNN International | cnn international | 80 |
| HLN (CNN Headline News) | hln cnn headline news | 52 |

Top suggestion: **CNN** with score 100 ✅

---

## Actions

| Action | Description |
|---|---|
| 🔍 Scan & Suggest EPG | Show suggestions as a text report in the UI |
| 📤 Export Suggestions to CSV | Save full results to `/data/exports/` |
| ✅ Apply Best Suggestions | Assign top match if auto-apply is enabled and score ≥ threshold |
| 📺 List Unmatched Channels | Quick list of channels with no EPG, no scoring |

---

## Files Created

- `/data/exports/epg_suggester_YYYYMMDD_HHMMSS.csv` — suggestion exports

---

## Troubleshooting

**No suggestions found**
- Lower the *Minimum Match Score* to 30–40 and re-scan
- Check that EPG sources are loaded in Dispatcharr (Settings → EPG)
- Make sure your EPG source filter isn't excluding the correct source

**Too many wrong suggestions**
- Raise the *Minimum Match Score* to 70–80
- Enable all three stripping options (geo, quality, misc)

**Authentication error**
- Verify URL is reachable from the Dispatcharr container
- Check username / password are correct admin credentials

**Channels not updating after Apply**
- Refresh the Dispatcharr Channels page (F5)
- Confirm Auto-Apply is enabled and threshold is not too high
