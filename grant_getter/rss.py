"""Generate an RSS 2.0 feed from Grants.gov opportunities."""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote
from xml.etree import ElementTree


ATOM_NAMESPACE = "http://www.w3.org/2005/Atom"

ElementTree.register_namespace(
    "atom",
    ATOM_NAMESPACE,
)


def grants_gov_detail_url(opportunity_id: Any) -> str:
    """Construct the public Grants.gov opportunity URL."""

    encoded_id = quote(str(opportunity_id), safe="")

    return (
        "https://www.grants.gov/search-results-detail/"
        f"{encoded_id}"
    )


def parse_iso_datetime(value: str) -> datetime:
    """Parse an ISO 8601 timestamp and ensure it has a timezone."""

    parsed = datetime.fromisoformat(value)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)

    return parsed.astimezone(timezone.utc)


def format_rss_date(value: str) -> str:
    """Convert an ISO timestamp to the date format expected by RSS."""

    parsed = parse_iso_datetime(value)
    return format_datetime(parsed, usegmt=True)


def opportunity_description(
    opportunity: dict[str, Any],
    search_names: set[str],
) -> str:
    """Create the human-readable description for one feed item."""

    agency_name = (
        opportunity.get("agencyName")
        or "Not supplied"
    )
    agency_code = (
        opportunity.get("agencyCode")
        or "Not supplied"
    )
    opportunity_number = (
        opportunity.get("number")
        or "Not supplied"
    )
    status = (
        opportunity.get("oppStatus")
        or "Not supplied"
    )
    open_date = (
        opportunity.get("openDate")
        or "Not supplied"
    )
    close_date = (
        opportunity.get("closeDate")
        or "Not supplied"
    )

    aln_values = opportunity.get("alnist") or []

    if isinstance(aln_values, list) and aln_values:
        aln_text = ", ".join(
            str(value)
            for value in aln_values
        )
    else:
        aln_text = "Not supplied"

    searches_text = ", ".join(sorted(search_names))

    return "\n".join(
        [
            f"Agency: {agency_name} ({agency_code})",
            f"Opportunity number: {opportunity_number}",
            f"Status: {status}",
            f"Open date: {open_date}",
            f"Close date: {close_date}",
            f"Assistance Listing: {aln_text}",
            f"Matched search: {searches_text}",
        ]
    )


def build_rss_tree(
    items: list[dict[str, Any]],
    *,
    title: str,
    description: str,
    feed_url: str,
    site_url: str,
    language: str = "en-us",
) -> ElementTree.ElementTree:
    """Build an RSS 2.0 XML document."""

    rss = ElementTree.Element(
        "rss",
        {"version": "2.0"},
    )

    channel = ElementTree.SubElement(rss, "channel")

    ElementTree.SubElement(
        channel,
        "title",
    ).text = title

    ElementTree.SubElement(
        channel,
        "link",
    ).text = site_url

    ElementTree.SubElement(
        channel,
        "description",
    ).text = description

    ElementTree.SubElement(
        channel,
        "language",
    ).text = language

    ElementTree.SubElement(
        channel,
        f"{{{ATOM_NAMESPACE}}}link",
        {
            "href": feed_url,
            "rel": "self",
            "type": "application/rss+xml",
        },
    )

    build_time = datetime.now(timezone.utc)

    ElementTree.SubElement(
        channel,
        "lastBuildDate",
    ).text = format_datetime(
        build_time,
        usegmt=True,
    )

    for stored_item in items:
        opportunity = stored_item["opportunity"]
        search_names = stored_item["search_names"]
        published_at = stored_item["published_at"]

        opportunity_id = str(opportunity["id"])
        detail_url = grants_gov_detail_url(
            opportunity_id
        )

        item = ElementTree.SubElement(
            channel,
            "item",
        )

        ElementTree.SubElement(
            item,
            "title",
        ).text = str(opportunity["title"])

        ElementTree.SubElement(
            item,
            "link",
        ).text = detail_url

        guid = ElementTree.SubElement(
            item,
            "guid",
            {"isPermaLink": "false"},
        )
        guid.text = f"grants-gov-{opportunity_id}"

        ElementTree.SubElement(
            item,
            "pubDate",
        ).text = format_rss_date(published_at)

        ElementTree.SubElement(
            item,
            "description",
        ).text = opportunity_description(
            opportunity,
            search_names,
        )

    return ElementTree.ElementTree(rss)


def write_rss_feed(
    items: list[dict[str, Any]],
    *,
    output_path: Path,
    title: str,
    description: str,
    feed_url: str,
    site_url: str,
    language: str = "en-us",
) -> None:
    """Build the RSS feed and write it atomically."""

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    tree = build_rss_tree(
        items=items,
        title=title,
        description=description,
        feed_url=feed_url,
        site_url=site_url,
        language=language,
    )

    temporary_path = output_path.with_suffix(
        output_path.suffix + ".tmp"
    )

    tree.write(
        temporary_path,
        encoding="utf-8",
        xml_declaration=True,
    )

    temporary_path.replace(output_path)