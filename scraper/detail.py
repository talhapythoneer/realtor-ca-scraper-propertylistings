"""Parses an individual realtor.ca listing page for the fields not available on
the search-results card: full postal-code address, description (for keyword
filtering), property/building type, and agent + brokerage contact info.
"""
import logging
from typing import Optional

from bs4 import BeautifulSoup

from .address import parse_address
from .models import Listing

logger = logging.getLogger("realtor_scraper")


def _text(node) -> str:
    if not node:
        return ""
    return node.get_text(strip=True).replace("\xa0", " ")


def _address_text(node) -> str:
    if not node:
        return ""
    for br in node.find_all("br"):
        br.replace_with(", ")
    return " ".join(node.get_text().replace("\xa0", " ").split())


def enrich_listing_with_detail_page(listing: Listing, html: str) -> Listing:
    soup = BeautifulSoup(html, "html.parser")

    full_address = _address_text(soup.select_one("#listingAddress")) or listing.full_address
    if full_address:
        listing.full_address = full_address
        parsed = parse_address(full_address)
        listing.unit = parsed["unit"]
        listing.street_address = parsed["street_address"]
        listing.address_city = parsed["address_city"] or listing.address_city
        listing.province = parsed["province"]
        listing.postal_code = parsed["postal_code"]

    mls = _text(soup.select_one("#MLNumberVal"))
    if mls:
        listing.mls_number = mls

    price = _text(soup.select_one("#listingPriceValue"))
    if price:
        listing.price = price

    for icon in soup.select(".listingIconCon"):
        label = _text(icon.select_one(".listingIconText")).lower()
        value = _text(icon.select_one(".listingIconNum"))
        if not value:
            continue
        if "bedroom" in label:
            listing.bedrooms = value
        elif "bathroom" in label:
            listing.bathrooms = value
        elif "square" in label:
            listing.square_footage = value

    description = _text(soup.select_one("#propertyDescriptionCon"))
    if description:
        listing.description = description

    for sub in soup.select(".propertyDetailsSectionContentSubCon"):
        label = _text(sub.select_one(".propertyDetailsSectionContentLabel")).lower()
        value = _text(sub.select_one(".propertyDetailsSectionContentValue"))
        if not value:
            continue
        if label == "property type":
            listing.property_type = value
        elif label == "building type":
            listing.building_type = value
        elif label in ("storeys", "stories"):
            listing.storeys = value
        elif label == "time on realtor.ca":
            # Authoritative "days/weeks/months on realtor.ca" figure, present on every
            # detail page. Overwrites the search-card tag (listing.listed_time_ago),
            # which realtor.ca only shows for very recently listed properties - leaving
            # it blank, and thus falling back to the scrape date, for anything older.
            listing.listed_time_ago = value

    agent_name = _text(soup.select_one(".realtorCardName"))
    if agent_name:
        listing.agent_name = agent_name

    agent_phone = _text(soup.select_one(".realtorCardPhone .realtorCardContactNumber"))
    if agent_phone:
        listing.agent_phone = agent_phone

    office_name = _text(soup.select_one(".officeCardName"))
    if office_name:
        listing.brokerage_name = office_name

    office_address = _address_text(soup.select_one(".officeCardAddress"))
    if office_address:
        listing.brokerage_address = office_address

    for phone_con in soup.select(".officeCardPhoneCon"):
        phone_node = phone_con.select_one(".officeCardPhone")
        if not phone_node:
            continue
        number = _text(phone_con.select_one(".officeCardContactNumber"))
        if not number:
            continue
        phone_type = (phone_node.get("data-type") or "").lower()
        if phone_type == "telephone":
            listing.brokerage_phone = number
        elif phone_type == "fax":
            listing.brokerage_fax = number

    return listing
