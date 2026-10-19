"""Run configured searches and export the results to a CSV file."""
# Run with python -m grant_getter.search_preview

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from grant_getter.cli import (
    collect_opportunities,
    create_api_client,
    load_config,
)
from grant_getter.rss import grants_gov_detail_url

OUTPUT_COLUMNS = [
    "id",
    "number",
    "title",
    "agencyCode",
    "agencyName",
    "openDate",
    "closeDate",
    "oppStatus",
    "docType",
    "alnist",
    "matchedSearches",
    "detailURL",
]


def csv_value(value: Any) -> str:
    """Convert an API value into a CSV-compatible string."""

    if value is None:
        return ""

    if isinstance(value, (list, dict)):
        return json.dumps(
            value,
            ensure_ascii=False,
        )

    return str(value)


def test_search(
    config_path: str | Path = "config.yaml",
    output_path: str | Path = "test.csv",
) -> Path:
    """
    Run all configured searches and write the results to CSV.

    This does not modify SQLite, RSS files, GitHub, or Slack.
    """

    config_path = Path(config_path)
    output_path = Path(output_path)

    config = load_config(config_path)
    client = create_api_client(config)

    collected = collect_opportunities(
        client=client,
        config=config,
    )

    output_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    with output_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=OUTPUT_COLUMNS,
        )

        writer.writeheader()

        for item in collected.values():
            opportunity = item["opportunity"]

            row = {
                field: csv_value(opportunity.get(field))
                for field in OUTPUT_COLUMNS
                if field not in {
                    "matchedSearches",
                    "detailURL",
                }
            }

            row["matchedSearches"] = "; ".join(
                sorted(item["search_names"])
            )

            row["detailURL"] = grants_gov_detail_url(
                opportunity["id"]
            )

            writer.writerow(row)

    print(
        f"Wrote {len(collected)} distinct opportunities "
        f"to {output_path.resolve()}."
    )

    return output_path


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Run the configured Grants.gov searches and "
            "export the results to CSV."
        )
    )

    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Configuration file path.",
    )

    parser.add_argument(
        "--output",
        type=Path,
        default=Path("test.csv"),
        help="CSV output path (default: ./test.csv).",
    )

    return parser.parse_args()


def main() -> int:
    """Run the CSV search preview."""

    arguments = parse_arguments()

    test_search(
        config_path=arguments.config,
        output_path=arguments.output,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())