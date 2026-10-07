#!/usr/bin/env python3
"""
realtor.ca new-listings scraper.

Reads target regions/cities/prices from input/input.csv, pulls newly listed
homes from realtor.ca for each one, drops listings that match a keyword in
input/excluded_keywords.csv (land-only / investor / teardown language), and
writes per-region CSV + XLSX files to output/:

  - <Region>_master.csv/.xlsx     cumulative, deduplicated by MLS number
  - <Region>_fresh_<timestamp>.csv/.xlsx   just this run's new listings

Excluded listings are logged to output/<Region>_excluded_listings_log.csv so they can
be reviewed and later used to train an AI-based filter.

Usage:
    python run_scraper.py
    python run_scraper.py --days-back 3
    python run_scraper.py --region "Greater Calgary"
    python run_scraper.py --region "Metro Vancouver" --city Vancouver --dry-run
    python run_scraper.py --refresh-geo
    python run_scraper.py --fresh-start

Run `python run_scraper.py --help` for the full list of options.
See README.md for setup instructions.
"""
import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from scraper.address import PROVINCE_ABBR_TO_NAME, parse_address
from scraper.browser import BrowserSession, is_dead_browser_error
from scraper.config import load_config, load_excluded_keywords, load_input_rows
from scraper.filters import apply_keyword_filter
from scraper.dateutils import estimate_listed_date
from scraper.geocode import get_geo_params, load_geo_cache, save_geo_cache
from scraper.http_fetch import DetailFetcher
from scraper.models import SearchRow
from scraper.search import SearchBlockedError, SearchSession, search_city
from scraper.storage import (
    append_excluded_log,
    archive_region_outputs,
    excluded_log_path,
    load_excluded_mls_numbers,
    load_master_mls_numbers,
    master_path,
    write_region_outputs,
)

ROOT = Path(__file__).resolve().parent
logger = logging.getLogger("realtor_scraper")


class FatalRunError(RuntimeError):
    """Something that will make every remaining city fail too - stop the run (saving what we have)."""


def parse_args():
    parser = argparse.ArgumentParser(description="Scrape newly listed homes from realtor.ca.")
    parser.add_argument("--input", default=str(ROOT / "input" / "input.csv"), help="Path to input.csv")
    parser.add_argument("--config", default=str(ROOT / "input" / "config.yaml"), help="Path to config.yaml")
    parser.add_argument(
        "--keywords", default=str(ROOT / "input" / "excluded_keywords.csv"), help="Path to excluded_keywords.csv"
    )
    parser.add_argument(
        "--days-back",
        type=int,
        default=None,
        help="Override 'listed since' window (in days) for every row this run, e.g. for a catch-up run.",
    )
    parser.add_argument("--region", default=None, help="Only run rows whose region matches this (case-insensitive).")
    parser.add_argument("--city", default=None, help="Only run rows whose city matches this (case-insensitive).")
    parser.add_argument(
        "--headless",
        dest="headless",
        action="store_true",
        default=None,
        help="Run with no visible browser window. Not recommended - realtor.ca's bot protection was "
        "confirmed in testing to block even the first page load in headless mode.",
    )
    parser.add_argument(
        "--no-headless", dest="headless", action="store_false", default=None, help="Show the browser window (the default)."
    )
    parser.add_argument(
        "--refresh-geo", action="store_true", help="Ignore the cached city->map lookup and re-resolve every city."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only run the searches (no listing detail pages, no output files written). Good for testing.",
    )
    parser.add_argument("--no-details", action="store_true", help="Skip visiting individual listing pages this run.")
    parser.add_argument(
        "--fresh-start",
        action="store_true",
        help="Move the selected regions' existing master/excluded/fresh files into output/archive_<timestamp>/ "
        "before running, so this run's fresh file lists everything in the days_back window, not just what's "
        "new since the last run.",
    )
    return parser.parse_args()


def setup_logging(log_dir: Path, level: str):
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"scraper_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.log"

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(fmt)
    logger.addHandler(stream_handler)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(fmt)
    logger.addHandler(file_handler)

    logger.info("Logging to %s", log_file)


def select_rows(rows, region_filter, city_filter):
    selected = []
    for row in rows:
        if not row.active:
            continue
        if region_filter and row.region.strip().lower() != region_filter.strip().lower():
            continue
        if city_filter and row.city.strip().lower() != city_filter.strip().lower():
            continue
        selected.append(row)
    return selected


def process_row(
    browser, search, fetcher, row: SearchRow, geo_cache: dict, config: dict, args, run_timestamp: str,
    seen_mls: set, stats: dict,
):
    scrape_cfg = config["scrape"]

    geo_params = get_geo_params(browser.driver, geo_cache, row, config["geo"], force_refresh=args.refresh_geo)
    if not geo_params:
        raise RuntimeError(f"could not work out a search area for '{row.city}'")

    matches = search_city(search, geo_params, row, scrape_cfg)
    stats["matched"] = len(matches)
    logger.info("%s, %s: %d listing(s) on realtor.ca match the filters.", row.city, row.region, len(matches))

    new_listings, batch_keys = [], set()
    for listing in matches:
        key = listing.mls_number or f"id:{listing.listing_id}"
        if key in seen_mls or key in batch_keys:
            continue
        batch_keys.add(key)
        new_listings.append(listing)
    stats["new"] = len(new_listings)

    logger.info(
        "%s, %s: %d new listing(s) not already in the master file or excluded log.",
        row.city, row.region, len(new_listings),
    )

    if args.dry_run:
        for listing in new_listings:
            print(json.dumps(listing.to_row(), ensure_ascii=False))
        seen_mls.update(batch_keys)
        return new_listings, []

    fetch_details = scrape_cfg.get("fetch_listing_details", True) and not args.no_details
    keywords = config["_keywords"]

    # The browser is sitting on a realtor.ca page with a challenge-passed session;
    # detail pages are fetched with a copy of it over plain HTTP (see http_fetch.py).
    # The description - what the keyword filter reads - is only on the detail page.
    if fetch_details:
        fetcher.enrich(new_listings, label=f"{row.city}, {row.region}")

    included, excluded = [], []
    for listing in new_listings:
        listing.first_seen_run = run_timestamp
        listing.date_scraped = datetime.now().isoformat(timespec="seconds")

        # The search API gives the exact date a listing went up; the relative
        # "Time on REALTOR.ca" text is only a fallback for when that was missing.
        listing.estimated_listed_date = listing.estimated_listed_date or estimate_listed_date(listing.listed_time_ago)

        if not listing.street_address:
            parsed = parse_address(listing.full_address)
            listing.unit = listing.unit or parsed["unit"]
            listing.street_address = parsed["street_address"]
            listing.address_city = listing.address_city or parsed["address_city"]
            listing.province = listing.province or parsed["province"]
            listing.postal_code = listing.postal_code or parsed["postal_code"]

        if not listing.province:
            listing.province = PROVINCE_ABBR_TO_NAME.get(row.province.strip().upper(), row.province)

        apply_keyword_filter(listing, keywords)
        (excluded if listing.excluded else included).append(listing)

    # Marked as seen only now: a row that fails part-way must not leave its
    # listings marked as seen, or the retry would skip them.
    seen_mls.update(batch_keys)
    stats["written"], stats["excluded"] = len(included), len(excluded)
    return included, excluded


def process_row_with_recovery(browser, search, fetcher, row, geo_cache, config, args, run_timestamp, seen_mls, stats):
    """process_row, retried once - after relaunching Chrome if it crashed or was closed.

    Raises FatalRunError when retrying can't help (Chrome won't stay up, or
    realtor.ca keeps refusing searches), so the run stops and says so instead
    of logging "0 listings" for every remaining city as if that were a result.
    """
    for attempt in (1, 2):
        try:
            return process_row(browser, search, fetcher, row, geo_cache, config, args, run_timestamp, seen_mls, stats)
        except SearchBlockedError as exc:
            if attempt == 2:
                raise FatalRunError(str(exc)) from exc
            logger.warning("%s: %s - restarting the browser and trying again.", row.city, exc)
        except Exception as exc:
            if is_dead_browser_error(exc):
                if attempt == 2:
                    raise FatalRunError(
                        "the Chrome window keeps closing or crashing - make sure nobody closes it while the "
                        "scraper runs, then run again"
                    ) from exc
                logger.error("%s: the Chrome window closed or crashed - restarting it and retrying this city.", row.city)
            elif attempt == 2:
                logger.exception("%s, %s failed twice - skipping it this run: %s", row.city, row.region, exc)
                stats["error"] = str(exc)
                return [], []
            else:
                logger.warning("%s, %s failed (%s) - retrying once.", row.city, row.region, exc)
                time.sleep(5)
                continue

        try:
            browser.restart()
        except Exception as exc:
            raise FatalRunError(f"couldn't restart Chrome: {exc}") from exc
        search.reset()
    return [], []


def log_summary(all_stats: list):
    logger.info("Per-city summary (Matched = listings on realtor.ca within the row's price/days filters):")
    logger.info("  %-18s %-26s %8s %6s %8s %9s", "Region", "City", "Matched", "New", "Written", "Excluded")
    for st in all_stats:
        if "error" in st:
            logger.info("  %-18s %-26s FAILED: %s", st["region"], st["city"], st["error"][:80])
        elif "matched" not in st:
            logger.info("  %-18s %-26s not run (run stopped early)", st["region"], st["city"])
        else:
            logger.info(
                "  %-18s %-26s %8d %6d %8s %9s", st["region"], st["city"], st["matched"], st.get("new", 0),
                st.get("written", "-"), st.get("excluded", "-"),
            )


def main():
    args = parse_args()
    config = load_config(Path(args.config))
    if args.headless is not None:
        config["scrape"]["headless"] = args.headless

    setup_logging(ROOT / config["logging"]["log_dir"], config["logging"]["level"])

    rows = load_input_rows(Path(args.input))
    if args.days_back is not None:
        for row in rows:
            row.days_back = args.days_back

    keywords = load_excluded_keywords(Path(args.keywords))
    config["_keywords"] = keywords
    logger.info("Loaded %d input row(s) and %d active excluded keyword(s).", len(rows), len(keywords))

    selected_rows = select_rows(rows, args.region, args.city)
    if not selected_rows:
        logger.error("No active input rows matched your filters. Nothing to do.")
        return

    rows_by_region = defaultdict(list)
    for row in selected_rows:
        rows_by_region[row.region].append(row)

    output_dir = ROOT / config["output"]["output_dir"]
    geo_cache_path = ROOT / config["geo"]["cache_file"]
    geo_cache = load_geo_cache(geo_cache_path)
    run_timestamp = datetime.now().strftime(config["output"]["timestamp_format"])

    if args.fresh_start and not args.dry_run:
        for region in rows_by_region:
            moved = archive_region_outputs(output_dir, region, config["output"], run_timestamp)
            if moved:
                logger.info("--fresh-start: moved %d old %s file(s) to %s", len(moved), region, moved[0].parent)

    grand_total_new = 0
    grand_total_excluded = 0
    all_stats = [{"region": row.region, "city": row.city} for row in selected_rows]
    stats_by_row = {id(row): st for row, st in zip(selected_rows, all_stats)}
    stopped_reason = None

    with BrowserSession(config) as browser:
        search = SearchSession(browser, config)
        fetcher = DetailFetcher(browser, config)
        for region, region_rows in rows_by_region.items():
            if stopped_reason:
                break
            logger.info("=== Region: %s (%d cities) ===", region, len(region_rows))
            m_path = master_path(output_dir, region, config["output"]["master_filename_template"], "csv")
            excl_log_path = excluded_log_path(output_dir, region, config["output"]["excluded_log_filename_template"])
            seen_mls = load_master_mls_numbers(m_path) | load_excluded_mls_numbers(excl_log_path)

            region_included, region_excluded = [], []
            for row in region_rows:
                try:
                    included, excluded = process_row_with_recovery(
                        browser, search, fetcher, row, geo_cache, config, args, run_timestamp, seen_mls,
                        stats_by_row[id(row)],
                    )
                except FatalRunError as exc:
                    stopped_reason = str(exc)
                    logger.error("Stopping the run at %s, %s: %s", row.city, row.region, exc)
                    break
                region_included.extend(included)
                region_excluded.extend(excluded)
                save_geo_cache(geo_cache_path, geo_cache)  # persist progress as we go

            if args.dry_run:
                logger.info("[DRY RUN] %s: %d new listing(s) found (no files written).", region, len(region_included))
                continue

            # Written even when the run stopped part-way, so nothing already scraped is lost.
            written = write_region_outputs(
                output_dir,
                region,
                region_included,
                config["output"]["formats"],
                config["output"]["master_filename_template"],
                config["output"]["fresh_filename_template"],
                run_timestamp,
            )
            append_excluded_log(excl_log_path, region_excluded)

            logger.info(
                "%s: %d new listing(s) written, %d excluded by keyword filter.",
                region,
                len(region_included),
                len(region_excluded),
            )
            for label, path in written.items():
                logger.info("  %s -> %s", label, path)

            grand_total_new += len(region_included)
            grand_total_excluded += len(region_excluded)

    save_geo_cache(geo_cache_path, geo_cache)
    log_summary(all_stats)
    if stopped_reason:
        logger.error(
            "Run stopped early: %s. Everything found before that point was saved - run again to pick up the "
            "remaining cities (listings already saved are skipped automatically).",
            stopped_reason,
        )
    logger.info(
        "Done. %d new listing(s) written across %d region(s), %d excluded by keyword filter.",
        grand_total_new,
        len(rows_by_region),
        grand_total_excluded,
    )


if __name__ == "__main__":
    main()
