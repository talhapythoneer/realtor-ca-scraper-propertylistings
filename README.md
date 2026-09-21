# realtor.ca New Listings Scraper

Scrapes newly-listed homes from realtor.ca for a set of target cities/regions
and price ranges, drops listings that look like land-only/investor/teardown
properties (via a keyword list), and writes the results to per-region CSV and
Excel files - a cumulative **master** file plus a **fresh** file for just
that run's new listings.

## 1. Folder structure

```
realtor_ca/
├── input/
│   ├── input.csv               <- regions, cities, price filters (edit this to change what gets scraped)
│   ├── excluded_keywords.csv   <- keywords that mark a listing as land/investor/teardown (edit this to tune filtering)
│   └── config.yaml             <- technical settings: proxy, timing, output format, etc.
├── output/                     <- created automatically; this is where your CSV/XLSX files land
├── data/geo_cache.json         <- created automatically; remembers each city's map location so future runs are faster
├── logs/                       <- created automatically; one log file per run
├── scraper/                    <- the scraper's Python package (you shouldn't need to edit this)
└── run_scraper.py              <- the script you actually run
```

## 2. One-time setup

Requires Python 3.10+ and Google Chrome installed (the scraper drives it via `undetected_chromedriver` - see §8 for why).

```bash
pip install -r requirements.txt
```

That's it - `undetected_chromedriver` downloads and manages its own matching driver binary automatically the first time it runs.

## 3. Running it

```bash
python run_scraper.py
```

On Windows you can also just double-click `run_windows.bat`.

Useful options (run `python run_scraper.py --help` for the full list):

| Option | What it does |
|---|---|
| `--days-back 3` | Overrides the "listed since" window for every city this run - e.g. for a catch-up run covering the days since your last scrape. |
| `--region "Greater Calgary"` | Only scrapes that one region (matches the `region` column in input.csv). |
| `--city Vancouver` | Only scrapes that one city. Combine with `--region` for testing a single row. |
| `--dry-run` | Fetches result-list pages only (skips listing detail pages and writes no files). Good for a quick check that a city/filter combination returns results. |
| `--no-details` | Skips visiting each listing's detail page (faster, but you lose description/agent/brokerage/full-address - the description is what the keyword filter runs against, so excluded listings won't be caught with this on). |
| `--refresh-geo` | Ignores the cached city location and re-resolves it. Use this if a city's results look wrong. |
| `--headless` | Runs with no visible browser window. **Not recommended** - see §8, this reliably gets blocked. |

**Recommended first run:** try one city with `--dry-run` before running everything:

```bash
python run_scraper.py --region "Greater Calgary" --city Calgary --dry-run
```

## 4. Editing input/input.csv

One row per city. Columns:

- **region** - groups cities into one set of output files (e.g. "Greater Calgary", "Metro Vancouver", "Vancouver Island"). Cities sharing a region name are combined into the same master/fresh files.
- **city** - display name, used in the output and in logs.
- **seo_slug** - *usually leave blank.* The scraper derives realtor.ca's URL slug from the city name automatically (e.g. "High River" → "high-river"). Only fill this in if a city fails to resolve or resolves to the wrong place - see Troubleshooting below.
- **province** - two-letter code (AB, BC, ON, ...).
- **price_min** / **price_max** - the price filter for that city. Leave `price_max` blank for no upper limit.
- **days_back** - "listed in the last N days" for that city. Leave blank to use `default_days_back` from config.yaml (7, i.e. weekly). Override with `--days-back` on the command line for a one-off catch-up run instead of editing every row.
- **active** - `Y`/`N`. Set to `N` to temporarily skip a city without deleting the row.
- **notes** - free text, ignored by the scraper. Some rows are pre-flagged `VERIFY: ...` - see Troubleshooting.

The file already contains the regions/cities/prices from our discussion (Greater Calgary, Metro Vancouver, Vancouver Island). Add, remove, or edit rows freely - no code changes needed to change your target areas.

## 5. Editing input/excluded_keywords.csv

Any listing whose description contains one of these keywords (case-insensitive) is excluded from the master/fresh files and logged separately instead (see below). Columns:

- **keyword** - the text to match.
- **match_field** - which field to check (`description` for the listing description text; leave as `description` unless you know you need something else).
- **active** - `Y`/`N` toggle.
- **notes** - free text.

This starts with a seed list covering the land/investor/teardown language discussed - add to it as you review results. This is the plain keyword-matching approach (no AI) - if you want to fine-tune an AI-based version of this later, `output/excluded_listings_log.csv` is exactly the training data you'll want (see below).

## 6. Output files

For each **region**, every run produces:

- `<Region>_master.csv` / `.xlsx` - every listing ever captured for that region, across all runs, deduplicated by MLS number. This is the running record used to detect duplicates.
- `<Region>_fresh_<timestamp>.csv` / `.xlsx` - only the new listings found in *this* run. This is what you'd send to the print shop for that week's mailer.

Each `.xlsx` file has two tabs (a plain `.csv` has no concept of tabs, so this split is Excel-only):

- **Raw Data** - every field the scraper captures, same as the `.csv`.
- **Print Shop** - just what the mailer needs: Unit, Street Address, City, Province, Postal Code, Price, Building Type, Date Listed.

Across all regions:

- `excluded_listings_log.csv` - listings that matched an excluded keyword, with which keyword matched. Kept cumulatively (not overwritten) so it can be reviewed and, later, used to train an AI-based version of this filter.

Fields captured per listing: MLS number, price, address split into unit / street address / city / province / postal code (plus the original combined address for reference), property type, building type, bedrooms/bathrooms/square footage, description, estimated listed date, listing URL, agent name and phone, brokerage name/phone/fax/address. **Emails are not included** - realtor.ca doesn't expose agent/brokerage email addresses publicly.

Price, bedrooms, bathrooms, square footage and storeys are written as real numbers (not text like `"$698,000"` or `"3 + 2"`), so they sort and filter correctly in Excel. Bedrooms combines realtor.ca's "above-grade + below-grade" notation into one total (`"3 + 2"` → `5`); square footage uses the lower bound when realtor.ca only gives a range or a `"1200+"` bucket. This normalization re-runs on the *entire* master file on every write, so older rows scraped before this existed get cleaned up automatically too - no separate migration step needed.

**Listed date accuracy:** `estimated_listed_date` is calculated from realtor.ca's own **"Time on REALTOR.ca"** field on each listing's detail page (e.g. "62 days"), not from the search-card "New" tag - that tag is only shown for the first few days after listing, so relying on it alone (as an earlier version of this scraper did) meant older listings silently fell back to the scrape date instead of their real listed date. This fix requires `fetch_listing_details: true` (the default) - with `--no-details`, there's no detail page to read this from, so the estimate falls back to the (less reliable) card tag.

## 7. How "new listings only" works

Before scraping a region, the scraper reads that region's existing master CSV (plus the shared excluded-listings log) and builds a set of MLS numbers already accounted for. Anything found in this run that's already in that set is skipped - and never gets its detail page re-fetched either, so previously-excluded listings aren't repeatedly re-checked on every run. There's no separate database or state file - the master CSV *is* the record, so you can open and inspect it any time.

This also answers the "what if I miss a week" scenario: just run with a larger `--days-back` (or edit the `days_back` column) to look further back than usual - anything already in the master file gets skipped automatically, so there's no risk of duplicates even with overlapping date ranges.

Results come back **newest-listed first** (realtor.ca's own default sort), and pagination takes advantage of that: once `consecutive_seen_to_stop` listings in a row (default 20, in config.yaml) turn out to already be in the master file, the scraper assumes everything after that point is old too and moves on to the next city, instead of paging all the way through a city's full result set every run.

## 8. realtor.ca's anti-bot protection, and how this scraper gets past it

realtor.ca runs bot-detection (Imperva/Distil-style) in front of both its pages and its internal search - exactly what was anticipated when scoping this project. Getting a live, filtered, multi-page search working reliably took solving a few distinct layers of it:

1. **The city-search autocomplete endpoint** (`/Services/Actions.asmx/GetAutocompleteResults`) returns a hard "blocked" response even from a brand-new browser session. This scraper avoids it entirely - city map locations are instead read from realtor.ca's own public SEO landing pages (e.g. `realtor.ca/ab/calgary/real-estate`), which are reliable and don't touch that endpoint at all.
2. **A browser-fingerprint check** (`/ping.html`), plus a second, stricter version of the same check that fires specifically when a live search executes. Both consistently returned 403 under Playwright - even driving the real installed Chrome binary with fingerprint overrides applied - and no amount of matching real browser behaviour (correct URLs, in-page navigation instead of full reloads) got past it. Switching the whole browser layer to **`undetected_chromedriver` run non-headless** resolved this completely: the exact same script that reliably 403/503'd under Playwright loads fine under it, and searches that never rendered a single result under Playwright return real listings within about a second. **Headless mode still gets blocked immediately, even on the very first page load** - this was confirmed directly in testing, so `scrape.headless: false` in config.yaml is a hard requirement of this specific site's protection, not just a suggestion.
3. **Pagination beyond page 1 needs a real click, not a URL change.** Once the search-execution block above was solved, a second issue turned up: rewriting `CurrentPage` in the URL hash and re-applying it silently returns page 1's results every time, no matter what page number is requested - confirmed by comparing MLS numbers directly across "pages" and finding them identical. Clicking the site's own visible "next page" control does work (its internal state carries forward context like `GeoIds`/`GeoName` that a hand-built hash doesn't reconstruct), so that's what this scraper does: page 1 is reached via a hash change (to apply price/date/sort filters), and every page after that via a real click on the next-page control, with a check that confirms the listings actually changed before treating a page as successfully loaded.

**Bottom line:** with `undetected_chromedriver` + `headless: false`, live multi-page filtered search has been confirmed working end-to-end in testing - e.g. a real run against Calgary at $800k+ correctly paginated 40 consecutive pages (440 listings) with zero blocks. If you still see it fail:

- Check the log for the specific error - `HTTP 403`/`503` on `ping.html` or a script under `/bundles/js/desktop/` means this protection kicked in for that request specifically, not a bug in the filters.
- The built-in random delays (`config.yaml` → `scrape.delay_between_pages_seconds` / `scrape.delay_between_listings_seconds`) and retry logic (`nav_retry_count`) exist to ride out transient versions of this - don't remove them.
- Running all ~40 cities back-to-back in one sitting is more likely to trip a block than spacing runs out or doing a handful of regions at a time.
- If blocking becomes a recurring problem at full scale despite the above, a **residential/rotating proxy** (see §9) is the fallback that was already scoped and budgeted for this project - a fresh IP identity is the standard fix for session/IP-reputation-based scoring. No code changes are needed to turn one on.

## 9. Adding a proxy later

In `input/config.yaml`:

```yaml
proxy:
  enabled: true
  server: "http://gateway.yourprovider.com:7000"
  username: "your-username"
  password: "your-password"
```

No code changes needed - the browser will route all traffic through it automatically.

## 10. Troubleshooting a specific city

If a city in input.csv fails with an error like *"Could not resolve a map location"* or *"HTTP 404"*:

1. Open a browser and search for that city on realtor.ca yourself.
2. Look at the resulting page's URL - if it's a plain landing page like `realtor.ca/bc/north-vancouver/real-estate`, copy the part between the province and `/real-estate` (here, `north-vancouver`).
3. Paste that into the `seo_slug` column for that row in input.csv.
4. Re-run with `--refresh-geo` for that city to force it to re-resolve.

A few rows are already flagged `VERIFY` in the notes column - these are cities where a single municipality name may cover two distinct areas on realtor.ca (the City vs. District of North Vancouver, the City vs. Township of Langley) or where the town has recently changed names (Diamond Valley). Confirm these manually before relying on their results.

## 11. Possible future additions (not built yet)

These came up in discussion and are worth keeping in mind, but are out of scope for this delivery:

- **AI-assisted filtering**: using the listing description and/or photos to catch teardown/vacant/investor listings that the keyword list misses. `config.yaml` has an `ai_filter` section already scaffolded (disabled) for this - `output/excluded_listings_log.csv` doubles as a starting training/review set for it.
- **Scheduling**: currently this is a manual, run-it-yourself script. It can be deployed to a small server later to run automatically (e.g. weekly) and push results somewhere like a Google Sheet or database instead of local files.
