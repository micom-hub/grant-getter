""" Command line calls for the Grant getter"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

import yaml

from grant_getter.api import GrantsGovClient
from grant_getter.rss import write_rss_feed
from grant_getter.slack import (
    build_grant_message,
    grants_gov_detail_url,
    parse_grants_gov_date,
    send_test_message,
    send_webhook,
)
from grant_getter.storage import GrantStore, utc_now

# Open a log
LOGGER = logging.getLogger(__name__)

# Path to config/db
CONFIG_PATH = Path("config.yaml")
DATABASE_PATH = Path("state/grants.sqlite3")

def load_config(path: Path) -> dict[str, Any]:
    """Load and validate Grant Getter's YAML configuration."""

    if not path.is_file():
        raise RuntimeError(
            f"Configuration file not found: {path}"
        )

    with path.open("r", encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file)

    if not isinstance(config, dict):
        raise TypeError(
            "The configuration root must be a YAML mapping."
        )

    api_config = config.get("api")

    if not isinstance(api_config, dict):
        raise TypeError(
            "The configuration is missing an 'api' mapping."
        )

    if not api_config.get("search_url"):
        raise TypeError(
            "The API configuration is missing 'search_url'."
        )

    defaults = api_config.get("defaults", {})

    if not isinstance(defaults, dict):
        raise TypeError(
            "'api.defaults' must be a YAML mapping."
        )

    notification_config = config.get("notifications")

    if not isinstance(notification_config, dict):
        raise TypeError(
            "The configuration is missing a "
            "'notifications' mapping."
        )

    maximum = notification_config.get(
        "max_new_per_run",
        10,
    )

    if not isinstance(maximum, int) or maximum <= 0:
        raise RuntimeError(
            "'notifications.max_new_per_run' must be "
            "a positive integer."
        )

    rss_config = config.get("rss")

    if not isinstance(rss_config, dict):
        raise TypeError(
            "The configuration is missing an 'rss' mapping."
        )

    required_rss_fields = {
        "output_path",
        "feed_url",
        "site_url",
        "title",
        "description",
    }

    missing_rss_fields = [
        field
        for field in sorted(required_rss_fields)
        if not rss_config.get(field)
    ]

    if missing_rss_fields:
        raise RuntimeError(
            "The RSS configuration is missing: "
            + ", ".join(missing_rss_fields)
        )

    max_items = rss_config.get("max_items", 50)

    if not isinstance(max_items, int) or max_items <= 0:
        raise RuntimeError(
            "'rss.max_items' must be a positive integer."
        )

    searches = config.get("searches")

    if not isinstance(searches, list) or not searches:
        raise RuntimeError(
            "The configuration must contain at least one search."
        )

    search_names: set[str] = set()

    for position, search in enumerate(searches, start=1):
        if not isinstance(search, dict):
            raise TypeError(
                f"Search #{position} must be a YAML mapping."
            )

        search_name = search.get("name")

        if not isinstance(search_name, str) or not search_name.strip():
            raise RuntimeError(
                f"Search #{position} needs a nonempty name."
            )

        if search_name in search_names:
            raise RuntimeError(
                f"Duplicate search name: {search_name!r}"
            )

        search_names.add(search_name)

        criteria = search.get("criteria")

        if not isinstance(criteria, dict) or not criteria:
            raise RuntimeError(
                f"Search {search_name!r} needs a "
                "nonempty criteria mapping."
            )

    return config


def create_api_client(
    config: dict[str, Any],
) -> GrantsGovClient:
    """Create a Grants.gov client from the YAML settings."""

    api_config = config["api"]

    return GrantsGovClient(
        search_url=str(api_config["search_url"]),
        timeout_seconds=int(
            api_config.get("timeout_seconds", 30)
        ),
        rows_per_page=int(
            api_config.get("rows_per_page", 100)
        ),
        max_pages_per_search=int(
            api_config.get("max_pages_per_search", 5)
        ),
    )


def collect_opportunities(
    client: GrantsGovClient,
    config: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """
    Run every configured search and deduplicate by opportunity ID.
    """

    defaults = config["api"].get("defaults", {})
    collected: dict[str, dict[str, Any]] = {}

    for configured_search in config["searches"]:
        search_name = configured_search["name"]
        criteria = configured_search["criteria"]

        search_results = client.search(
            search_name=search_name,
            criteria=criteria,
            defaults=defaults,
        )

        LOGGER.info(
            "Search %r returned %d distinct results.",
            search_name,
            len(search_results),
        )

        for opportunity in search_results:
            opportunity_id = str(opportunity["id"])

            if opportunity_id not in collected:
                collected[opportunity_id] = {
                    "opportunity": opportunity,
                    "search_names": set(),
                }

            collected[opportunity_id]["search_names"].add(
                search_name
            )

    LOGGER.info(
        "All searches produced %d distinct opportunities.",
        len(collected),
    )

    return collected


def sorted_items(
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Sort opportunities by open date and then title."""

    return sorted(
        items,
        key=lambda item: (
            parse_grants_gov_date(
                item["opportunity"].get("openDate")
            ),
            str(item["opportunity"].get("title", "")),
        ),
        reverse=True,
    )


def print_opportunities(
    items: list[dict[str, Any]],
) -> None:
    """Print collected opportunities for human review."""

    ordered_items = sorted_items(items)

    if not ordered_items:
        print(
            "No opportunities matched the configured searches."
        )
        return

    for position, item in enumerate(
        ordered_items,
        start=1,
    ):
        opportunity = item["opportunity"]
        search_names = item["search_names"]

        aln_values = opportunity.get("alnist") or []

        if isinstance(aln_values, list) and aln_values:
            aln_text = ", ".join(
                str(value)
                for value in aln_values
            )
        else:
            aln_text = "Not supplied"

        detail_url = grants_gov_detail_url(
            opportunity["id"]
        )

        print()
        print(f"{position}. {opportunity['title']}")
        print(
            f"   ID:          {opportunity['id']}"
        )
        print(
            "   Number:      "
            f"{opportunity.get('number') or 'Not supplied'}"
        )
        print(
            "   Agency code: "
            f"{opportunity.get('agencyCode') or 'Not supplied'}"
        )
        print(
            "   Agency name: "
            f"{opportunity.get('agencyName') or 'Not supplied'}"
        )
        print(
            "   Status:      "
            f"{opportunity.get('oppStatus') or 'Not supplied'}"
        )
        print(
            "   Open date:   "
            f"{opportunity.get('openDate') or 'Not supplied'}"
        )
        print(
            "   Close date:  "
            f"{opportunity.get('closeDate') or 'Not supplied'}"
        )
        print(
            "   Document:    "
            f"{opportunity.get('docType') or 'Not supplied'}"
        )
        print(f"   ALN:         {aln_text}")
        print(
            "   Searches:    "
            + ", ".join(sorted(search_names))
        )
        print(f"   URL:         {detail_url}")

    print()
    print(
        f"Total distinct opportunities: "
        f"{len(ordered_items)}"
    )


def require_slack_webhook() -> str:
    """Read the Slack webhook from the process environment."""

    webhook_url = os.environ.get(
        "SLACK_WEBHOOK_URL",
        "",
    ).strip()

    if not webhook_url:
        raise RuntimeError(
            "SLACK_WEBHOOK_URL is not set."
        )

    return webhook_url


def search_command(
    client: GrantsGovClient,
    config: dict[str, Any],
) -> None:
    """Search Grants.gov without modifying state."""

    collected = collect_opportunities(client, config)
    print_opportunities(list(collected.values()))


def bootstrap_command(
    client: GrantsGovClient,
    config: dict[str, Any],
    database_path: Path,
) -> None:
    """Record existing results without notifying Slack."""

    with GrantStore(database_path) as store:
        previous_bootstrap = store.get_metadata(
            "bootstrapped_at"
        )

        if previous_bootstrap is not None:
            raise RuntimeError(
                "This state database was already bootstrapped "
                f"at {previous_bootstrap}."
            )

        collected = collect_opportunities(client, config)
        items = list(collected.values())

        store.save_items(items)
        store.set_metadata("bootstrapped_at", utc_now())

        print(
            f"Bootstrap complete. Recorded {len(items)} "
            "existing opportunities without notifying Slack."
        )


def test_slack_command(
    config: dict[str, Any],
) -> None:
    """Send one test message to Slack."""

    webhook_url = require_slack_webhook()

    timeout_seconds = int(
        config["api"].get("timeout_seconds", 30)
    )

    send_test_message(
        webhook_url=webhook_url,
        timeout_seconds=timeout_seconds,
    )

    print("Slack test message sent successfully.")


def run_command(
    client: GrantsGovClient,
    config: dict[str, Any],
    database_path: Path,
) -> None:
    """Notify Slack about previously unseen opportunities."""

    with GrantStore(database_path) as store:
        bootstrapped_at = store.get_metadata(
            "bootstrapped_at"
        )

        if bootstrapped_at is None:
            raise RuntimeError(
                "The state database has not been bootstrapped. "
                "Run:\n"
                "python -m grant_getter.cli bootstrap"
            )

        collected = collect_opportunities(client, config)

        new_items = [
            item
            for opportunity_id, item in collected.items()
            if not store.has_opportunity(opportunity_id)
        ]

        print(
            f"Found {len(new_items)} previously unseen "
            "opportunities."
        )

        if not new_items:
            store.set_metadata(
                "last_successful_run",
                utc_now(),
            )

            print("No Slack notification is needed.")
            return

        maximum = int(
            config["notifications"].get(
                "max_new_per_run",
                10,
            )
        )

        if len(new_items) > maximum:
            raise RuntimeError(
                "Spam safety limit activated. Found "
                f"{len(new_items)} unseen opportunities, but "
                f"max_new_per_run is {maximum}. No Slack "
                "message was sent, and these opportunities "
                "were not saved. Run the search command to "
                "inspect the current results."
            )
        store.publish_items(new_items)

        generate_feed(
            store=store,
            config=config,
        )

        store.set_metadata(
            "last_successful_run",
            utc_now(),
        )

        print(
            f"Published {len(new_items)} new opportunities "
            "to the RSS feed."
        )
        


def parse_arguments() -> argparse.Namespace:
    """Define and parse Grant Getter's command-line interface."""

    parser = argparse.ArgumentParser(
        prog="grant-getter",
        description=(
            "Search Grants.gov and notify Slack "
            "about new matching opportunities."
        ),
    )

    parser.add_argument(
        "--config",
        type=Path,
        default=CONFIG_PATH,
        help=(
            "Configuration file path "
            "(default: config.yaml)."
        ),
    )

    parser.add_argument(
        "--database",
        type=Path,
        default=DATABASE_PATH,
        help=(
            "SQLite state database path "
            "(default: state/grants.sqlite3)."
        ),
    )

    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable detailed debug logging.",
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    subparsers.add_parser(
        "search",
        help=(
            "Print current matches without changing state "
            "or contacting Slack."
        ),
    )

    subparsers.add_parser(
        "bootstrap",
        help=(
            "Record current matches without contacting Slack."
        ),
    )

    subparsers.add_parser(
        "test-slack",
        help="Send one Slack connectivity test.",
    )

    subparsers.add_parser(
        "run",
        help=(
            "Notify Slack about previously unseen matches."
        ),
    )

    subparsers.add_parser(
    "build-feed",
    help="Generate feed.xml from published database items.",
)

    return parser.parse_args()

def generate_feed(
    store: GrantStore,
    config: dict[str, Any],
) -> None:
    """Generate feed.xml from the RSS items stored in SQLite."""

    rss_config = config["rss"]
    maximum_items = int(
        rss_config.get("max_items", 50)
    )

    feed_items = store.get_feed_items(
        limit=maximum_items
    )

    write_rss_feed(
        items=feed_items,
        output_path=Path(rss_config["output_path"]),
        title=str(rss_config["title"]),
        description=str(rss_config["description"]),
        feed_url=str(rss_config["feed_url"]),
        site_url=str(rss_config["site_url"]),
        language=str(
            rss_config.get("language", "en-us")
        ),
    )

    print(
        f"Wrote {len(feed_items)} RSS items to "
        f"{rss_config['output_path']}."
    )


# runs the argument command returns 0 if successful, 1 if error (logged)
def main() -> int:
    """Run the selected command and return a process exit code."""

    arguments = parse_arguments()

    logging.basicConfig(
        level=(
            logging.DEBUG
            if arguments.verbose
            else logging.INFO
        ),
        format=(
            "%(asctime)s %(levelname)s "
            "%(name)s: %(message)s"
        ),
    )

    try:
        config = load_config(arguments.config)

        if arguments.command == "test-slack":
            test_slack_command(config)
            return 0

        client = create_api_client(config)

        if arguments.command == "search":
            search_command(client, config)

        elif arguments.command == "bootstrap":
            bootstrap_command(
                client=client,
                config=config,
                database_path=arguments.database,
            )

        elif arguments.command == "run":
            run_command(
                client=client,
                config=config,
                database_path=arguments.database,
            )
        elif arguments.command == "build-feed":
          with GrantStore(arguments.database) as store:
              generate_feed(store, config)

        else:
            raise RuntimeError(
                f"Unknown command: {arguments.command}"
            )
          
        return 0

    except Exception as error:
        LOGGER.exception(
            "Grant Getter failed: %s",
            error,  # noqa: TRY401
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
