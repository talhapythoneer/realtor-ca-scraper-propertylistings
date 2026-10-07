"""Loading of config.yaml, input.csv and excluded_keywords.csv."""
import csv
import logging
from pathlib import Path
from typing import List

import yaml

from .models import SearchRow

logger = logging.getLogger("realtor_scraper")


DEFAULT_CONFIG = {
    "scrape": {
        "headless": False,
        "page_load_timeout_ms": 45000,
        "page_load_strategy": "eager",
        "nav_retry_count": 3,
        "delay_between_api_calls_seconds": [1, 2],
        "delay_between_listings_seconds": [1, 1],
        "api_records_per_page": 200,
        "default_days_back": 7,
        "sort": "6-D",
        "transaction_type_id": 2,
        "property_type_group_id": 1,
        "property_search_type_id": 0,
        "currency": "CAD",
        "fetch_listing_details": True,
        "detail_fetch_method": "http",
        "http_max_requests_per_minute": 50,
        "http_concurrency": 2,
        "http_cooldown_seconds": 90,
        "http_timeout_seconds": 30,
        "http_max_consecutive_failures": 5,
        "http_impersonate": "chrome",
        "user_agent": "",
    },
    "proxy": {
        "enabled": False,
        "provider": "",
        "server": "",
        "username": "",
        "password": "",
    },
    "ai_filter": {
        "enabled": False,
        "provider": "",
        "api_key_env": "AI_FILTER_API_KEY",
        "model": "",
        "confidence_threshold": 0.7,
    },
    "output": {
        "formats": ["csv", "xlsx"],
        "output_dir": "output",
        "master_filename_template": "{region}_master.{ext}",
        "fresh_filename_template": "{region}_fresh_{timestamp}.{ext}",
        "excluded_log_filename_template": "{region}_excluded_listings_log.csv",
        "timestamp_format": "%Y-%m-%d_%H%M%S",
    },
    "geo": {
        "cache_file": "data/geo_cache.json",
        "force_refresh": False,
        "bbox_padding_km": 0,
    },
    "logging": {
        "log_dir": "logs",
        "level": "INFO",
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(path: Path) -> dict:
    if not path.exists():
        logger.warning("Config file %s not found, using built-in defaults.", path)
        return DEFAULT_CONFIG
    with open(path, "r", encoding="utf-8") as f:
        user_config = yaml.safe_load(f) or {}
    return _deep_merge(DEFAULT_CONFIG, user_config)


def _to_bool(value: str, default: bool = True) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in ("y", "yes", "true", "1")


def _to_int(value: str):
    if value is None or value.strip() == "":
        return None
    cleaned = value.replace(",", "").replace("$", "").strip()
    return int(float(cleaned))


def load_input_rows(path: Path) -> List[SearchRow]:
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    rows: List[SearchRow] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for i, raw in enumerate(reader, start=2):  # row 1 is the header
            region = (raw.get("region") or "").strip()
            city = (raw.get("city") or "").strip()
            if not region or not city:
                logger.warning("Skipping input.csv line %d: region/city is blank.", i)
                continue
            try:
                rows.append(
                    SearchRow(
                        region=region,
                        city=city,
                        seo_slug=(raw.get("seo_slug") or "").strip(),
                        province=(raw.get("province") or "").strip(),
                        price_min=_to_int(raw.get("price_min")),
                        price_max=_to_int(raw.get("price_max")),
                        days_back=_to_int(raw.get("days_back")),
                        active=_to_bool(raw.get("active"), default=True),
                        notes=(raw.get("notes") or "").strip(),
                        map_url=(raw.get("map_url") or "").strip(),
                    )
                )
            except ValueError as exc:
                logger.warning("Skipping input.csv line %d: %s", i, exc)
    return rows


def load_excluded_keywords(path: Path) -> List[dict]:
    if not path.exists():
        logger.warning("Excluded keywords file %s not found, no keyword filtering will be applied.", path)
        return []

    keywords = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            keyword = (raw.get("keyword") or "").strip()
            if not keyword:
                continue
            if not _to_bool(raw.get("active"), default=True):
                continue
            keywords.append(
                {
                    "keyword": keyword.lower(),
                    "match_field": (raw.get("match_field") or "description").strip().lower(),
                    "notes": (raw.get("notes") or "").strip(),
                }
            )
    return keywords
