""" This is the Slack incoming webhook """

from __future__ import annotations

import html
from datetime import datetime
from typing import Any
from urllib.parse import quote

import requests

# FORMATTING STUFF ------

class SlackError(RuntimeError):
    """Raised when Grant Getter cannot deliver a Slack message."""

# Return a url to a given grant's public page
def grants_gov_detail_url(opportunity_id: Any) -> str:
    """Construct the public Grants.gov page for an opportunity."""

    encoded_id = quote(str(opportunity_id), safe="")

    return (
        "https://www.grants.gov/search-results-detail/"
        f"{encoded_id}"
    )

# stack overflow says this is helpful because slack handles &, <, >, specially
def escape_slack_text(value: Any) -> str:
    """Slack mrkdwn helper."""

    return html.escape(str(value), quote=False)

#  Parse a date or error
def parse_grants_gov_date(value: Any) -> datetime:
    """
    Parse the MM/DD/YYYY format used by search2.

    An unparseable or missing date sorts before valid dates.
    """

    if not isinstance(value, str):
        return datetime.min

    try:
        return datetime.strptime(value, "%m/%d/%Y")
    except ValueError:
        return datetime.min

# Compose the message that will be put in the slack
def build_grant_message(
    items: list[dict[str, Any]],
    heading: str,
) -> dict[str, Any]:
    """Build one Slack webhook payload containing all new grants."""

    sorted_items = sorted(
        items,
        key=lambda item: (
            parse_grants_gov_date(
                item["opportunity"].get("openDate")
            ),
            str(item["opportunity"].get("title", "")),
        ),
        reverse=True,
    )

    opportunity_count = len(sorted_items)
    noun = (
        "opportunity"
        if opportunity_count == 1
        else "opportunities"
    )

    blocks: list[dict[str, Any]] = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": heading[:150],
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": (
                        f"{opportunity_count} new matching {noun}."
                    ),
                }
            ],
        },
    ]
     # Compose the slack message
    for item in sorted_items:
        opportunity = item["opportunity"]
        search_names = item["search_names"]

        opportunity_id = opportunity["id"]
        url = grants_gov_detail_url(opportunity_id)

        title = escape_slack_text(opportunity["title"])
        number = escape_slack_text(
            opportunity.get("number") or "Not supplied"
        )
        agency_code = escape_slack_text(
            opportunity.get("agencyCode") or "Not supplied"
        )
        agency_name = escape_slack_text(
            opportunity.get("agencyName") or "Not supplied"
        )
        status = escape_slack_text(
            opportunity.get("oppStatus") or "Not supplied"
        )
        open_date = escape_slack_text(
            opportunity.get("openDate") or "Not supplied"
        )
        close_date = escape_slack_text(
            opportunity.get("closeDate") or "Not supplied"
        )

        aln_values = opportunity.get("alnist") or []

        if isinstance(aln_values, list) and aln_values:
            aln_text = ", ".join(
                escape_slack_text(value)
                for value in aln_values
            )
        else:
            aln_text = "Not supplied"

        matched_searches = escape_slack_text(
            ", ".join(sorted(search_names))
        )

        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*<{url}|{title}>*\n"
                        f"*Agency:* {agency_name} "
                        f"(`{agency_code}`)\n"
                        f"*Opportunity number:* `{number}`\n"
                        f"*Status:* {status}\n"
                        f"*Open date:* {open_date}\n"
                        f"*Close date:* {close_date}\n"
                        f"*Assistance Listing:* {aln_text}\n"
                        f"*Matched search:* {matched_searches}"
                    ),
                },
            }
        )

        blocks.append({"type": "divider"})

    return {
        "text": (
            f"{heading}: "
            f"{opportunity_count} new {noun}"
        ),
        "blocks": blocks,
    }

# ACTUAL SLACK SENDING STUFF ------

# Send a big json payload to the slack webhook
def send_webhook(
    webhook_url: str,
    payload: dict[str, Any],
    timeout_seconds: int = 30,
) -> None:
    """Send one JSON payload to a Slack incoming webhook."""

    if not webhook_url.startswith(
        "https://hooks.slack.com/"
    ):
        raise SlackError(
            "The Slack webhook does not look like an expected "
            "hooks.slack.com URL."
        )

    try:
        response = requests.post(
            webhook_url,
            json=payload,
            timeout=timeout_seconds,
        )

        response.raise_for_status()

    except requests.RequestException as error:
        raise SlackError(
            f"Slack webhook request failed: {error}"
        ) from error

    if response.text.strip().lower() != "ok":
        raise SlackError(
            "Slack returned an unexpected response: "
            f"{response.text[:500]!r}"
        )

# Test messaging function
def send_test_message(
    webhook_url: str,
    timeout_seconds: int = 30,
) -> None:
    """Send a simple Slack connectivity test."""

    payload = {
        "text": "Grant Getter test successful",
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        ":white_check_mark: "
                        "*Grant Getter test successful*\n"
                        "Grant Getter can post to this channel."
                    ),
                },
            }
        ],
    }
    # send it 
    send_webhook(
        webhook_url=webhook_url,
        payload=payload,
        timeout_seconds=timeout_seconds,
    )