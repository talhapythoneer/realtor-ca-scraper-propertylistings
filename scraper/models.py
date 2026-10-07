"""Data structures shared across the scraper."""
from dataclasses import dataclass
from typing import Optional


# Column order used for every CSV/XLSX file written by the scraper.
OUTPUT_COLUMNS = [
    "region",
    "city",
    "mls_number",
    "listing_id",
    "price",
    "unit",
    "street_address",
    "address_city",
    "full_address",
    "postal_code",
    "province",
    "property_type",
    "building_type",
    "bedrooms",
    "bathrooms",
    "square_footage",
    "storeys",
    "listed_time_ago",
    "estimated_listed_date",
    "listing_url",
    "image_url",
    "agent_name",
    "agent_phone",
    "brokerage_name",
    "brokerage_phone",
    "brokerage_fax",
    "brokerage_address",
    "description",
    "excluded",
    "excluded_keyword",
    "first_seen_run",
    "date_scraped",
]


@dataclass
class Listing:
    region: str = ""
    city: str = ""
    mls_number: str = ""
    listing_id: str = ""
    price: str = ""
    unit: str = ""
    street_address: str = ""
    address_city: str = ""  # city as written in the postal address - what a mailer needs
    full_address: str = ""
    postal_code: str = ""
    province: str = ""
    property_type: str = ""
    building_type: str = ""
    bedrooms: str = ""
    bathrooms: str = ""
    square_footage: str = ""
    storeys: str = ""
    listed_time_ago: str = ""
    estimated_listed_date: str = ""
    listing_url: str = ""
    image_url: str = ""
    agent_name: str = ""
    agent_phone: str = ""
    brokerage_name: str = ""
    brokerage_phone: str = ""
    brokerage_fax: str = ""
    brokerage_address: str = ""
    description: str = ""
    excluded: bool = False
    excluded_keyword: str = ""
    first_seen_run: str = ""
    date_scraped: str = ""

    def to_row(self) -> dict:
        return {name: getattr(self, name) for name in OUTPUT_COLUMNS}


@dataclass
class SearchRow:
    """One row from input/input.csv."""
    region: str
    city: str
    seo_slug: str
    province: str
    price_min: Optional[int]
    price_max: Optional[int]
    days_back: Optional[int]
    active: bool
    notes: str = ""
    # Optional realtor.ca map URL (copied from the browser address bar) whose visible
    # map area defines the search area - for towns realtor.ca has no boundary for.
    map_url: str = ""
