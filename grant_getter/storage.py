""" SQLite storage for the Grant Getter opportunities 
    
Thought of this as a lightweight way to deduplicate IDs so the Slack isn't spammed with past opportunities each time it is queried. """
import json  # noqa: I001
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Self

import sqlite3

# Timestamp helper
def utc_now() -> str:
    """Return the current UTC time in ISO 8601 format."""

    return datetime.now(UTC).isoformat()

# Class that stores grant.gov opportunities we've already seen in a db
# don't re-spam the slack for every polling

# Because we aren't polling very frequently, we don't really need a LISTEN/NOTIFY of something like PostgreSQL
class GrantStore:
    """Store seen Grants.gov opportunities in a SQLite database."""

    def __init__(self, database_path: Path) -> None:
        database_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        self.connection = sqlite3.connect(database_path)
        self.connection.row_factory = sqlite3.Row

        self._create_tables()

    # if db tables aren't there, make it
    def _create_tables(self) -> None:
        """Create the database tables if they do not already exist."""

        # make opportunities table
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS opportunities (
                opportunity_id TEXT PRIMARY KEY,
                source_json TEXT NOT NULL,
                search_names_json TEXT NOT NULL,
                first_seen_at TEXT NOT NULL
            )
            """
        )

        # make a metadata table with key + val
        # contains when bootstrapping was (for initial values), and the last run
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )

        # RSS feed table
        # has Grant Getter seen the opportunity before
        # has it been added to the publc RSS feed 
        self.connection.execute(
          """
          CREATE TABLE IF NOT EXISTS feed_items (
              opportunity_id TEXT PRIMARY KEY,
              published_at TEXT NOT NULL
          )
          """
      )

        # commit it
        self.connection.commit()

    # check the db to see if an opportunity is stored
    def has_opportunity(self, opportunity_id: str) -> bool:
        """Return whether an opportunity ID is already stored."""

        # pull the argument opportunity id
        row = self.connection.execute(
            """
            SELECT 1
            FROM opportunities
            WHERE opportunity_id = ?
            """,
            (opportunity_id,),
        ).fetchone()

        return row is not None

    # store everything we collected, don't overwrite existing ones
    def save_items(
        self,
        items: list[dict[str, Any]],
    ) -> None:
        """Store collected opportunities without replacing existing ones."""

        first_seen_at = utc_now()

        with self.connection:
            for item in items:
                opportunity = item["opportunity"]
                search_names = item["search_names"]

                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO opportunities (
                        opportunity_id,
                        source_json,
                        search_names_json,
                        first_seen_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        str(opportunity["id"]),
                        json.dumps(
                            opportunity,
                            sort_keys=True,
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            sorted(search_names),
                            ensure_ascii=False,
                        ),
                        first_seen_at,
                    ),
                )

    # get metadata from one application
    def get_metadata(self, key: str) -> str | None:
        """Retrieve one application metadata value."""

        row = self.connection.execute(
            """
            SELECT value
            FROM metadata
            WHERE key = ?
            """,
            (key,),
        ).fetchone()

        if row is None:
            return None

        return str(row["value"])

    # set/replace changing metadata
    def set_metadata(self, key: str, value: str) -> None:
        """Create or replace one application metadata value."""

        with self.connection:
            self.connection.execute(
                """
                INSERT INTO metadata (key, value)
                VALUES (?, ?)
                ON CONFLICT(key)
                DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
    # return the number of stored opportunities
    def count_opportunities(self) -> int:
        """Return the number of stored opportunities."""

        row = self.connection.execute(
            """
            SELECT COUNT(*) AS opportunity_count
            FROM opportunities
            """
        ).fetchone()

        return int(row["opportunity_count"])

    def close(self) -> None:
        """Close the SQLite connection."""

        self.connection.close()

    # stores items and marks them as going to the RSS
    def publish_items(
        self,
        items: list[dict[str, Any]],
    ) -> None:
        """
        Store new opportunities and mark them as RSS feed items.

        Both changes occur in one database transaction.
        """

        published_at = utc_now()

        with self.connection:
            for item in items:
                opportunity = item["opportunity"]
                search_names = item["search_names"]
                opportunity_id = str(opportunity["id"])

                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO opportunities (
                        opportunity_id,
                        source_json,
                        search_names_json,
                        first_seen_at
                    )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        opportunity_id,
                        json.dumps(
                            opportunity,
                            sort_keys=True,
                            ensure_ascii=False,
                        ),
                        json.dumps(
                            sorted(search_names),
                            ensure_ascii=False,
                        ),
                        published_at,
                    ),
                )

                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO feed_items (
                        opportunity_id,
                        published_at
                    )
                    VALUES (?, ?)
                    """,
                    (
                        opportunity_id,
                        published_at,
                    ),
                )
    # Retrieve recently published RSS items fro msqlite
    def get_feed_items(
      self,
      limit: int = 50,
  ) -> list[dict[str, Any]]:
      """Return the most recently published RSS items."""

      rows = self.connection.execute(
          """
          SELECT
              opportunities.source_json,
              opportunities.search_names_json,
              feed_items.published_at
          FROM feed_items
          JOIN opportunities
            ON opportunities.opportunity_id =
              feed_items.opportunity_id
          ORDER BY feed_items.published_at DESC
          LIMIT ?
          """,
          (limit,),
      ).fetchall()

      items: list[dict[str, Any]] = []

      for row in rows:
          items.append(
              {
                  "opportunity": json.loads(
                      row["source_json"]
                  ),
                  "search_names": set(
                      json.loads(row["search_names_json"])
                  ),
                  "published_at": row["published_at"],
              }
          )

      return items

    # dunders to safely open and close connection in case of errors

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

