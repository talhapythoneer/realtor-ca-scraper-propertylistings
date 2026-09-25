#!/usr/bin/env python3
"""
realtor.ca new-listings scraper.

Reads target regions/cities/prices from input/input.csv, pulls newly listed
homes from realtor.ca for each one, drops listings that match a keyword in
input/excluded_keywords.csv (land-only / investor / teardown language), and
writes per-region CSV + XLSX files to output/:

  - <Region>_master.csv/.xlsx     cumulative, deduplicated by MLS number
  - <Region>_fresh_<timestamp>.csv/.xlsx   just this run's new listings

Excluded listings are logged to output/excluded_listings_log.csv so they can
be reviewed and later used to train an AI-based filter.

Usage:
    python run_scraper.py
    python run_scraper.py --days-back 3
    python run_scraper.py --region "Greater Calgary"
    python run_scraper.py --region "Metro Vancouver" --city Vancouver --dry-run
    python run_scraper.py --refresh-geo

Run `python run_scraper.py --help` for the full list of options.
See README.md for setup instructions.
"""
import argparse
import json
import logging
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from scraper.address import PROVINCE_ABBR_TO_NAME, parse_address
from scraper.browser import launch_browser
from scraper.config import load_config, load_excluded_keywords, load_input_rows
from scraper.filters import apply_keyword_filter
from scraper.dateutils import estimate_listed_date
from scraper.geocode import get_geo_params, load_geo_cache, save_geo_cache
from scraper.http_fetch import DetailFetcher
from scraper.models import SearchRow
from scraper.search import fetch_all_pages
from scraper.storage import (
    append_excluded_log,
    load_excluded_mls_numbers,
    load_master_mls_numbers,
    master_path,
    write_region_outputs,
)

ROOT = Path(__file__).resolve().parent
logger = logging.getLogger("realtor_scraper")


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
        help="Only fetch result-list pages (no listing detail pages, no output files written). Good for testing.",
    )
    parser.add_argument("--no-details", action="store_true", help="Skip visiting individual listing pages this run.")
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
    driver, fetcher: DetailFetcher, row: SearchRow, geo_cache: dict, config: dict, args, run_timestamp: str, seen_mls: set
):
    scrape_cfg = config["scrape"]

    geo_params = get_geo_params(driver, geo_cache, row, force_refresh=args.refresh_geo)
    if not geo_params:
        logger.error("Could not resolve a map location for '%s', %s - skipping this city.", row.city, row.region)
        return [], []

    card_listings = fetch_all_pages(driver, geo_params, row, scrape_cfg, seen_mls)
    logger.info("%s, %s: %d listing(s) found in search results.", row.city, row.region, len(card_listings))

    new_listings = []
    for listing in card_listings:
        key = listing.mls_number or f"id:{listing.listing_id}"
        if key in seen_mls:
            continue
        seen_mls.add(key)
        new_listings.append(listing)

    logger.info("%s, %s: %d new listing(s) not already in the master file.", row.city, row.region, len(new_listings))

    if args.dry_run:
        for listing in new_listings:
            print(json.dumps(listing.to_row(), ensure_ascii=False))
        return new_listings, []

    fetch_details = scrape_cfg.get("fetch_listing_details", True) and not args.no_details
    keywords = config["_keywords"]

    # Pagination above leaves the browser holding a fresh, challenge-passed session;
    # detail pages are fetched with a copy of it over plain HTTP (see http_fetch.py).
    if fetch_details:
        fetcher.enrich(new_listings, label=f"{row.city}, {row.region}")

    included, excluded = [], []
    for listing in new_listings:
        listing.first_seen_run = run_timestamp
        listing.date_scraped = datetime.now().isoformat(timespec="seconds")

        # Computed *after* the detail-page fetch above, since that's what fills in
        # listed_time_ago from realtor.ca's "Time on REALTOR.ca" field - a reliable
        # figure present on every listing, unlike the search-card "new listing" tag
        # (listing.listed_time_ago's other source), which realtor.ca only shows for
        # the first few days after listing and leaves blank otherwise.
        listing.estimated_listed_date = estimate_listed_date(listing.listed_time_ago)

        # Fallback address parsing for --no-details runs / failed detail fetches,
        # where enrich_listing_with_detail_page() above never ran: the search-card
        # full_address (no postal code, but has unit/street/province) still works.
        if not listing.street_address:
            parsed = parse_address(listing.full_address)
            listing.unit = listing.unit or parsed["unit"]
            listing.street_address = parsed["street_address"]
            listing.province = listing.province or parsed["province"]
            listing.postal_code = listing.postal_code or parsed["postal_code"]

        if not listing.province:
            listing.province = PROVINCE_ABBR_TO_NAME.get(row.province.strip().upper(), row.province)

        apply_keyword_filter(listing, keywords)
        print(json.dumps(listing.to_row(), ensure_ascii=False))
        (excluded if listing.excluded else included).append(listing)

    return included, excluded


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

    grand_total_new = 0
    grand_total_excluded = 0
    excluded_log_path = output_dir / config["output"]["excluded_log_filename"]

    with launch_browser(config) as driver:
        fetcher = DetailFetcher(driver, config)
        for region, region_rows in rows_by_region.items():
            logger.info("=== Region: %s (%d cities) ===", region, len(region_rows))
            m_path = master_path(output_dir, region, config["output"]["master_filename_template"], "csv")
            seen_mls = load_master_mls_numbers(m_path) | load_excluded_mls_numbers(excluded_log_path)

            region_included, region_excluded = [], []
            for row in region_rows:
                try:
                    included, excluded = process_row(
                        driver, fetcher, row, geo_cache, config, args, run_timestamp, seen_mls
                    )
                except Exception as exc:
                    logger.exception("Unexpected error processing %s, %s: %s", row.city, row.region, exc)
                    continue
                region_included.extend(included)
                region_excluded.extend(excluded)
                save_geo_cache(geo_cache_path, geo_cache)  # persist progress as we go

            if args.dry_run:
                logger.info("[DRY RUN] %s: %d new listing(s) found (no files written).", region, len(region_included))
                continue

            written = write_region_outputs(
                output_dir,
                region,
                region_included,
                config["output"]["formats"],
                config["output"]["master_filename_template"],
                config["output"]["fresh_filename_template"],
                run_timestamp,
            )
            append_excluded_log(excluded_log_path, region_excluded)

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
    logger.info(
        "Done. %d new listing(s) written across %d region(s), %d excluded by keyword filter.",
        grand_total_new,
        len(rows_by_region),
        grand_total_excluded,
    )


if __name__ == "__main__":
    main()
