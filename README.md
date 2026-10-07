# realtor.ca New Listings Scraper

Built by [Talha Pythoneer](https://www.talhapythoneer.com), web scraping and AI agents.

Scrapes newly-listed homes from realtor.ca for a set of target cities/regions
and price ranges, drops listings that look like land-only/investor/teardown
properties (via a keyword list), and writes the results to per-region CSV and
Excel files - a cumulative **master** file plus a **fresh** file for just
that run's new listings.

## Quick Start (start here - no technical knowledge needed)

**What this does:** every time you run it, this looks through realtor.ca for
newly-listed homes in the cities you care about, drops ones that look like
teardown/land-only/investor listings, and saves the rest into Excel files you
can open normally.

**The very first time you use it:**

1. Make sure [Python](https://www.python.org/downloads/) and
   [Google Chrome](https://www.google.com/chrome/) are installed. When
   installing Python, tick the box that says **"Add Python to PATH"** on the
   first screen - this matters.
2. Double-click **`setup_windows.bat`** (Windows) or **`setup_mac.command`** (Mac) in
   this folder. It installs everything the scraper needs and tells you if
   anything's missing. You only do this once.
   - **On Mac**, the first time you open either `.command` file, macOS will
     likely refuse and warn that it can't verify it's free of malware - this
     is normal for any script that isn't a paid, Apple-notarized app, not a
     real detection. See "Mac security warning" right below for the fix.

**Every time you want new listings:**

1. Double-click **`run_windows.bat`** (Windows) or **`run_mac.command`** (Mac).
2. A Chrome window will pop up on its own and start browsing realtor.ca -
   this is normal, leave it alone and let it run in the background. Depending
   on how many cities are on the list, it can take anywhere from a few
   minutes to over an hour.
3. When it finishes, the black window will print a line starting with
   `Done.` and then wait for a key press - press any key to close it.
4. Open the **`output`** folder - your results are waiting there as Excel
   files, one set per region (e.g. `Greater_Calgary_fresh_2026-09-25_...xlsx`).

**Which file to actually open:** each region gets two kinds of files -

- **`<Region>_master.xlsx`** - every listing ever found for that region,
  building up over time. This is your permanent record; don't delete it.
- **`<Region>_fresh_<date-and-time>.xlsx`** - just the listings that are new
  **since your previous run**. **This is the file to look at each time** -
  it's short and it's exactly what's new since last time. It is *not* "every
  listing from the last 7 days": if you ran it two days ago, it only holds the
  last two days' new listings, because the rest are already in the master
  file. To get one file covering the whole window, run with `--fresh-start`
  (see §3).

That's the whole routine: run `run_windows.bat`/`run_mac.command` whenever
you want fresh listings (daily, weekly, whenever suits you), then open that
run's newest `_fresh_...xlsx` file for each region.

Want to add/remove a city or change a price range? See "Editing
input/input.csv" below - it's a plain spreadsheet, no code involved. If
something looks broken, jump to "Troubleshooting" further down, or send the
`logs` folder to whoever set this up for you.

### Mac security warning ("cannot be opened" / looks like a malware warning)

The first time you try to open `setup_mac.command` or `run_mac.command`, macOS
will likely block it with a message like *"Apple could not verify 'setup_mac.command'
is free of malware."* This is standard macOS behaviour for **any** script that
isn't a signed, paid Apple Developer app - it happens to every unsigned
script ever shared this way, including this one. It is not a real detection.
Try these in order:

1. **Right-click** (or Control-click) the file → choose **Open** from the
   menu (don't just double-click). A different dialog appears - if it has an
   **Open** button, click it. You won't be asked again after this.
2. **If that dialog only offers "Move to Trash"/"Cancel"** (no Open button -
   this happens on newer macOS versions): open **System Settings → Privacy &
   Security**, scroll down, and you'll see a line like *"'setup_mac.command' was
   blocked to protect your Mac."* Click **Open Anyway**, confirm with your
   password/Touch ID, then try opening the file again.
3. **Guaranteed fix if the above is finicky:** open **Terminal** (Spotlight
   search → type "Terminal") and run, from inside this folder:
   ```bash
   xattr -d com.apple.quarantine setup_mac.command run_mac.command
   ```
   This removes the flag macOS attaches to anything downloaded through a
   browser. Afterwards, double-clicking works normally, with no more warnings.

### Mac: "certificate verify failed" / SSL errors when running the scraper

If Python was installed via the official installer from python.org (as
opposed to Homebrew), it ships its own OpenSSL instead of using macOS's
trust store, so any HTTPS request fails with `ssl.SSLCertVerificationError:
... unable to get local issuer certificate` until a one-time fix is run.
`setup_mac.command` now runs this fix automatically - but if you set things up
before that was added, or it still happens, fix it directly: open Finder →
Applications → the "Python 3.x" folder (matching your installed version) →
double-click **"Install Certificates.command"** inside it. Then try running
the scraper again.

## 1. Folder structure

```
realtor_ca/
├── setup_windows.bat  <- Windows: double-click once, the first time only
├── setup_mac.command  <- Mac: double-click once, the first time only
├── run_windows.bat    <- Windows: double-click every time you want to scrape
├── run_mac.command    <- Mac: double-click every time you want to scrape
├── input/
│   ├── input.csv               <- regions, cities, price filters (edit this to change what gets scraped)
│   ├── excluded_keywords.csv   <- keywords that mark a listing as land/investor/teardown (edit this to tune filtering)
│   └── config.yaml             <- technical settings: proxy, timing, output format, etc.
├── output/                     <- created automatically; this is where your CSV/XLSX files land
├── data/geo_cache.json         <- created automatically; remembers each city's map location so future runs are faster
├── logs/                       <- created automatically; one log file per run
├── scraper/                    <- the scraper's Python package (you shouldn't need to edit this)
└── run_scraper.py              <- the script run_windows.bat calls under the hood
```

## 2. One-time setup

**Easiest way:** double-click `setup_windows.bat` (Windows) or `setup_mac.command`
(Mac). It checks that Python and Chrome are installed, tells you clearly if
something's missing (with a link to get it), and installs the rest
automatically. See the Quick Start above.

**Manual way** (if you'd rather use a terminal): requires Python 3.10+ and
Google Chrome installed (the scraper drives Chrome via `undetected_chromedriver`
- see §8 for why), then:

```bash
pip install -r requirements.txt
```

(On Mac, use `pip3` instead of `pip` if plain `pip` isn't found.)

That's it either way - `undetected_chromedriver` downloads and manages its
own matching driver binary automatically the first time it runs.

## 3. Running it

**Easiest way:** double-click `run_windows.bat` (Windows) or
`run_mac.command` (Mac). See the Quick Start above.

**Manual way:**

```bash
python run_scraper.py
```

(On Mac, use `python3` instead of `python` if plain `python` isn't found.)

**Advanced options** (open a terminal for these - run `python run_scraper.py --help` for the full list):

| Option | What it does |
|---|---|
| `--days-back 3` | Overrides the "listed since" window for every city this run - e.g. for a catch-up run covering the days since your last scrape. |
| `--region "Greater Calgary"` | Only scrapes that one region (matches the `region` column in input.csv). |
| `--city Vancouver` | Only scrapes that one city. Combine with `--region` for testing a single row. |
| `--dry-run` | Runs the searches only (skips listing detail pages and writes no files). Good for a quick check that a city/filter combination returns results. |
| `--fresh-start` | Moves the existing master, excluded-log and fresh files for the regions being run into `output/archive_<date-and-time>/` first (nothing is deleted), so this run treats every listing in the `days_back` window as new. Use it once after changing price ranges, or whenever you want one complete file for the whole window. |
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
- **seo_slug** - the realtor.ca URL slug for that city (e.g. "High River" → `high-river`, from `realtor.ca/ab/high-river/real-estate`). This is how the scraper finds the city's official boundary on realtor.ca. Can be left blank - the scraper then guesses it from the city name.
- **province** - two-letter code (AB, BC, ON, ...).
- **price_min** / **price_max** - the price filter for that city. Leave `price_max` blank for no upper limit.
- **days_back** - "listed in the last N days" for that city. Leave blank to use `default_days_back` from config.yaml (7, i.e. weekly). Override with `--days-back` on the command line for a one-off catch-up run instead of editing every row.
- **active** - `Y`/`N`. Set to `N` to temporarily skip a city without deleting the row.
- **map_url** *(optional column - add it to the header if you need it)* - a realtor.ca map address to define the search area by hand. Search for the town in realtor.ca's own search box, then copy the whole address bar (it contains `LatitudeMax=...&LongitudeMin=...`) into this column. Only needed if the automatic area for a small town (see below) isn't right.

**Adding towns:** just add a row - region, city, province, prices, `active` = `Y`. Use an existing region name to have it land in that region's files, or a new region name to get a new set of files. Places realtor.ca has no official boundary for (small towns and hamlets such as Bragg Creek, Langdon, Bowser, Chemainus) are searched by the map area around the town instead, the same way realtor.ca's own search box does it. That area is looked up automatically on OpenStreetMap the first time and remembered in `data/geo_cache.json`. The log shows which method each city used (`GeoIds=...` or `map area lat ...`). Run a new town once with `--dry-run --city "<Town>"` to check the count looks right.

## 5. Editing input/excluded_keywords.csv

Any listing whose description contains one of these keywords (case-insensitive) is excluded from the master/fresh files and logged separately instead (see below). Columns:

- **keyword** - the text to match.
- **match_field** - which field to check (`description` for the listing description text; leave as `description` unless you know you need something else).
- **active** - `Y`/`N` toggle.
- **notes** - free text.

This starts with a seed list covering the land/investor/teardown language discussed - add to it as you review results. This is the plain keyword-matching approach (no AI) - if you want to fine-tune an AI-based version of this later, each region's `output/<Region>_excluded_listings_log.csv` is exactly the training data you'll want (see below).

## 6. Output files

For each **region**, every run produces:

- `<Region>_master.csv` / `.xlsx` - every listing ever captured for that region, across all runs, deduplicated by MLS number. This is the running record used to detect duplicates.
- `<Region>_fresh_<timestamp>.csv` / `.xlsx` - only the new listings found in *this* run. This is what you'd send to the print shop for that week's mailer.
- `<Region>_excluded_listings_log.csv` - listings from that region that matched an excluded keyword, with which keyword matched. Kept cumulatively per region (not overwritten), same as the master file, so each region's log can be reviewed on its own and, later, used to train an AI-based version of this filter.

Each `.xlsx` file has two tabs (a plain `.csv` has no concept of tabs, so this split is Excel-only):

- **Raw Data** - every field the scraper captures, same as the `.csv`.
- **Print Shop** - just what the mailer needs: Unit, Street Address, City, Province, Postal Code, Price, Building Type, Date Listed. **City** here is the city written in the listing's postal address (e.g. "North Vancouver", "Langley"), not the search-area name from input.csv (e.g. "North Vancouver District", "Langley City"), since it's what goes on the envelope.

Fields captured per listing: MLS number, price, address split into unit / street address / city (`address_city`) / province / postal code (plus the original combined address for reference, and `city` = the input.csv row it was found under), property type, building type, bedrooms/bathrooms/square footage, description, estimated listed date, listing URL, agent name and phone, brokerage name/phone/fax/address. **Emails are not included** - realtor.ca doesn't expose agent/brokerage email addresses publicly.

Price, bedrooms, bathrooms, square footage and storeys are written as real numbers (not text like `"$698,000"` or `"3 + 2"`), so they sort and filter correctly in Excel. Bedrooms combines realtor.ca's "above-grade + below-grade" notation into one total (`"3 + 2"` → `5`); square footage uses the lower bound when realtor.ca only gives a range or a `"1200+"` bucket. This normalization re-runs on the *entire* master file on every write, so older rows scraped before this existed get cleaned up automatically too - no separate migration step needed.

**Listed date:** `estimated_listed_date` is the exact date the listing went up on realtor.ca, taken from the timestamp realtor.ca's search returns for each listing (in the listing's own timezone). `listed_time_ago` keeps realtor.ca's own wording (e.g. "3 days") for reference.

**Unit numbers:** realtor.ca writes units differently per MLS board - `408, 310 12 Avenue SW` (Calgary), `115-1925 18 Ave NE`, and in BC just a space: `2203 305 MORRISSEY ROAD`. All three are split into Unit (`2203`) and Street Address (`305 MORRISSEY ROAD`). Numbered streets without a unit (`5993 143 STREET`, `1234 56A Avenue`) are left whole. The address columns are re-derived for the whole master file on every write, so rows from earlier runs get the unit split too.

## 7. How "new listings only" works

Before scraping a region, the scraper reads that region's existing master CSV (plus the shared excluded-listings log) and builds a set of MLS numbers already accounted for. Anything found in this run that's already in that set is skipped - and never gets its detail page re-fetched either, so previously-excluded listings aren't repeatedly re-checked on every run. There's no separate database or state file - the master CSV *is* the record, so you can open and inspect it any time.

This also answers the "what if I miss a week" scenario: just run with a larger `--days-back` (or edit the `days_back` column) to look further back than usual - anything already in the master file gets skipped automatically, so there's no risk of duplicates even with overlapping date ranges.

Each city is searched with its price range and `days_back` window applied by realtor.ca itself, using the same search service realtor.ca's map page uses, 200 listings per request. So every listing in the window is read every run - there's no page cap and no "stop early" guess. If a city has more than 600 matches (the most realtor.ca returns for one search), the price range is split automatically and each half searched separately. As a safety net, every listing's price and listed date are checked again against the row's filters before anything is written; if realtor.ca ever returns something outside them, it's dropped and the log says so with a WARNING.

At the end of every run the log prints a per-city table - **Matched** (listings on realtor.ca within the filters right now), **New** (not seen in a previous run), **Written** and **Excluded**. Compare a city's Matched number with a realtor.ca search using the same filters if a count ever looks off.

## 8. realtor.ca's anti-bot protection, and how this scraper gets past it

realtor.ca runs bot-detection (Imperva/Distil-style) in front of both its pages and its internal search - exactly what was anticipated when scoping this project. Getting a live, filtered, multi-page search working reliably took solving a few distinct layers of it:

1. **The city-search autocomplete endpoint** (`/Services/Actions.asmx/GetAutocompleteResults`) returns a hard "blocked" response even from a brand-new browser session. This scraper avoids it entirely - city map locations are instead read from realtor.ca's own public SEO landing pages (e.g. `realtor.ca/ab/calgary/real-estate`), which are reliable and don't touch that endpoint at all.
2. **A browser-fingerprint check** (`/ping.html`), plus a second, stricter version of the same check that fires specifically when a live search executes. Both consistently returned 403 under Playwright - even driving the real installed Chrome binary with fingerprint overrides applied - and no amount of matching real browser behaviour (correct URLs, in-page navigation instead of full reloads) got past it. Switching the whole browser layer to **`undetected_chromedriver` run non-headless** resolved this completely: the exact same script that reliably 403/503'd under Playwright loads fine under it, and searches that never rendered a single result under Playwright return real listings within about a second. **Headless mode still gets blocked immediately, even on the very first page load** - this was confirmed directly in testing, so `scrape.headless: false` in config.yaml is a hard requirement of this specific site's protection, not just a suggestion.
3. **Search results come from realtor.ca's own search service, called from inside the browser.** realtor.ca's map page loads its results from a JSON service (`api2.realtor.ca/Listing.svc/PropertySearch_Post`) that takes the area, price range, "listed in the last N days", sort and page size. The scraper calls that service from inside the open Chrome page, so each request carries the browser's own cookies and fingerprint, exactly like the site's own requests. The service only answers once the browser holds a session token (`cf_api_tok`) that realtor.ca's map page sets when it loads, so the scraper opens the map page first and re-opens it whenever a search request is refused. (An earlier version set the filters in the URL of each city's landing page instead - that page turned out to ignore them, so price and date filters were never actually applied. This was fixed in October 2026.)
4. **Listing detail pages are fetched over plain HTTP, not the browser.** They're needed for the listing description (which the keyword filter reads - the search service leaves it out). Once a city's search is done, the browser is holding a session that has already passed the checks above. The scraper copies that session's cookies (via CDP, including HttpOnly ones) plus the browser's real user agent, `sec-ch-ua` and `Accept-Language` headers into [`curl_cffi`](https://github.com/lexiforest/curl_cffi) HTTP sessions (a `requests`-style client that reproduces Chrome's TLS handshake - plain `httpx`/`requests` were confirmed to get a 403 from realtor.ca even with every browser cookie attached), then fetches that city's new listing pages without the browser. realtor.ca's Cloudflare protection rate-limits listing pages to about 60 a minute. Above that, it answers every request with a 429 "Security Check" page for about a minute, and copying fresh cookies from the browser doesn't lift it early. So all HTTP requests share one rate cap (`scrape.http_max_requests_per_minute`, default 50). If a challenge still comes back, HTTP pauses for `scrape.http_cooldown_seconds` (default 90) while the browser keeps loading listings from the queue, then HTTP resumes with fresh cookies. To go back to browser-only detail fetching, set `scrape.detail_fetch_method: "browser"`.

**Bottom line:** with `undetected_chromedriver` + `headless: false`, the filtered search has been confirmed working end-to-end against all 44 configured cities (October 2026). If you still see it fail:

- Check the log for the specific error - `Search request failed ... request refused` means realtor.ca refused a search; the scraper re-opens the map page and retries by itself, and only stops the run (saving everything found so far) if that keeps failing.
- If the Chrome window crashes or gets closed mid-run, the scraper reopens it and retries that city once. If it closes again, the run stops with a clear message instead of carrying on with empty results - everything found up to then is saved, so just run it again.
- The built-in random delays (`config.yaml` → `scrape.delay_between_api_calls_seconds` / `scrape.delay_between_listings_seconds` / `scrape.http_max_requests_per_minute`) and retry logic (`nav_retry_count`) exist to ride out transient versions of this - don't remove them.
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

If a city shows 0 Matched run after run, or its counts look wrong, check which search area it's using - the log line `<City>: searching GeoIds=...` or `<City>: searching map area lat ...` at the start of each city:

1. Load `realtor.ca/<province>/<slug>/real-estate` yourself (the URL the scraper is using - `<slug>` is the `seo_slug` column if set, otherwise the city name lowercased with spaces turned into hyphens).
2. **The URL loading without a 404 is not enough** - the generic fallback page loads fine too. Check that the page title is the city's own (not the generic "MLS® & Real Estate Map | REALTOR.ca") and that it shows real listing cards, not a "no results" panel.
3. If it's the generic fallback, search realtor.ca's own search box for the city by hand and see what page it lands you on - the correct slug is the part of that URL between the province and `/real-estate`. Some places don't have their own page at all and only show up as part of a containing municipality or regional district (e.g. a hamlet under a rural county) - if so, that's the row's real target, not the hamlet name.
4. Paste the working slug into the `seo_slug` column for that row in input.csv, then re-run with `--refresh-geo` for that city to force it to re-resolve.

Bragg Creek, Langdon, Bowser and Chemainus have no realtor.ca boundary and are searched by map area (located on OpenStreetMap). If a map area turns out too small or too large, either paste a realtor.ca map URL for that town into its `map_url` column (see §4), or widen every map-area town with `geo.bbox_padding_km` in config.yaml. After changing either, run with `--refresh-geo` once.

## 11. Possible future additions (not built yet)

These came up in discussion and are worth keeping in mind, but are out of scope for this delivery:

- **AI-assisted filtering**: using the listing description and/or photos to catch teardown/vacant/investor listings that the keyword list misses. `config.yaml` has an `ai_filter` section already scaffolded (disabled) for this - the per-region `output/<Region>_excluded_listings_log.csv` files double as a starting training/review set for it.
- **Scheduling**: currently this is a manual, run-it-yourself script. It can be deployed to a small server later to run automatically (e.g. weekly) and push results somewhere like a Google Sheet or database instead of local files.
