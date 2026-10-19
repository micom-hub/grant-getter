# Functions for communication with the Grants.gov search api
from __future__ import annotations

import logging
import time
from typing import Any

import requests

# initiate logger to track searches, retries, or any wacky stuff
LOGGER = logging.getLogger(__name__)


# custom exception error for Grants.gov not being retrievable
class GrantsGovError(RuntimeError):
    """Raised when the Grants.gov API cannot be queried successfully."""


# These are the search fields Grant Getter currently supports.
#
# Things we search for:
# SUPPORTED_SEARCH_FIELDS = {
#     "keyword",
#     "oppNum",
#     "eligibilities",
#     "agencies",
#     "oppStatuses",
#     "aln",
#     "fundingCategories",
# }
# Expected fields that should be returned
# OPPORTUNITY_FIELDS = {
#     "id",
#     "number",
#     "title",
#     "agencyCode",
#     "agencyName",
#     "openDate",
#     "closeDate",
#     "oppStatus",
#     "docType",
#     "alnist",
#   }


# Package configs into a class
class GrantsGovClient:
    """Client for the Grants.gov search2 endpoint."""

    def __init__(
        self,
        search_url: str,
        timeout_seconds: int = 30,
        rows_per_page: int = 100,
        max_pages_per_search: int = 5,
    ) -> None:
        self.search_url = search_url
        self.timeout_seconds = timeout_seconds
        self.rows_per_page = rows_per_page
        self.max_pages_per_search = max_pages_per_search

        self.session = requests.Session()
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": ("grant-getter (automated Grants.gov monitor :) )"),
            }
        )

        # https://grants.gov/api/common/search2 - sample response for fields list
        # These are the search fields currently supported for requests
        self.SUPPORTED_SEARCH_FIELDS = {
            "keyword",
            "oppNum",
            "eligibilities",
            "agencies",
            "oppStatuses",
            "aln",
            "fundingCategories",
        }

        # These are the opportunity fields supported
        self.OPPORTUNITY_FIELDS = {
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
        }

    def _post_with_retries(
        self,
        payload: dict[str, Any],  # what's being posted
        attempts: int = 4,  # retry attempts
    ) -> dict[str, Any]:
        """Send one search request, retrying temporary failures."""

        delay_seconds = 2

        # try 'attempts' times
        for attempt in range(1, attempts + 1):
            try:
                response = self.session.post(
                    self.search_url,
                    json=payload,
                    timeout=self.timeout_seconds,
                )

                # HTTP 429 means the api is rate-limiting us.
                # HTTP 5xx means Grants.gov encountered a server error.
                # Both may be temporary, so we can retry.
                if response.status_code == 429 or response.status_code >= 500:
                    raise requests.HTTPError(
                        f"Temporary HTTP status {response.status_code}",
                        response=response,
                    )
                # Look for 400 bad requests, rejected
                if 400 <= response.status_code < 500:
                    raise GrantsGovError(
                        "Grants.gov rejected the request with HTTP "
                        f"{response.status_code}: {response.text[:500]}"
                    )

                # Raise an exception for other unsuccessful HTTP statuses,
                # such as 400, 403, or 404.
                response.raise_for_status()

                body = response.json()

                if not isinstance(body, dict):
                    raise GrantsGovError(
                        "Grants.gov returned JSON, but its top-level "
                        "value was not an object."
                    )

                return body

            # one of the GrantsGov errors that we can't retry with
            except GrantsGovError:
                # If not one of the above (based on what I gathered from their coes list), probably not worth retrying
                raise

            except (
                requests.RequestException,
                requests.JSONDecodeError,
            ) as error:
                if attempt == attempts:
                    raise GrantsGovError(
                        f"Grants.gov request failed after {attempts} attempts: {error}"
                    ) from error

                # write to log
                LOGGER.warning(
                    "Grants.gov request attempt %d/%d failed: %s. "
                    "Retrying in %d seconds.",
                    attempt,
                    attempts,
                    error,
                    delay_seconds,
                )

                time.sleep(delay_seconds)
                delay_seconds *= 2

        raise GrantsGovError("Unexpected request retry failure.")

    def _validate_search_parameters(
        self,
        parameters: dict[str, Any],
    ) -> None:
        """Check search params for anything not supported, and throw a fit if present."""

        unsupported_fields = set(parameters) - self.SUPPORTED_SEARCH_FIELDS

        if unsupported_fields:
            formatted_fields = ", ".join(sorted(unsupported_fields))

            raise GrantsGovError(
                "Unsupported Grants.gov search field(s): "
                f"{formatted_fields}. Supported fields are: "
                + ", ".join(sorted(self.SUPPORTED_SEARCH_FIELDS))
            )

    @classmethod
    def _validate_response(
        cls,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate the search2 response and return its data object."""

        errorcode = body.get("errorcode")
        message = body.get("msg", "")

        # grantsgov error that we didn't retry
        if errorcode != 0:
            raise GrantsGovError(
                "Grants.gov reported an application-level error. "
                f"errorcode={errorcode!r}, msg={message!r}"
            )

        data = body.get("data")

        # no data object returned
        if not isinstance(data, dict):
            raise GrantsGovError(
                "The Grants.gov response did not contain a valid 'data' object."
            )

        hits = data.get("oppHits")

        # no hits :(
        if not isinstance(hits, list):
            raise GrantsGovError(
                "The Grants.gov response did not contain a valid 'data.oppHits' list."
            )

        hit_count = data.get("hitCount")

        if not isinstance(hit_count, int):
            raise GrantsGovError(
                "The Grants.gov response did not contain an integer "
                "'data.hitCount' value."
            )

        return data

    @classmethod
    def _validate_opportunity(
        cls,
        opportunity: Any,
    ) -> dict[str, Any]:
        """
        Validate one oppHits item (a grant that was returned)
        """

        # check json
        if not isinstance(opportunity, dict):
            raise GrantsGovError("A data.oppHits item was not a JSON object.")

        opportunity_id = opportunity.get("id")

        # needs to have an id
        if opportunity_id in (None, ""):
            raise GrantsGovError("A data.oppHits item did not contain an 'id'.")

        # needs a title
        if "title" not in opportunity:
            raise GrantsGovError("A data.oppHits item did not contain a 'title'.")

        # Make a copy so we don't change it
        return opportunity.copy()

    def search(
        self,
        search_name: str,
        criteria: dict[str, Any],
        defaults: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """
        Run one configured search.

        Each returned dictionary is an unchanged data.oppHits record
        using the Grants.gov source field names.
        """

        self._validate_search_parameters(defaults)
        self._validate_search_parameters(criteria)

        search_parameters = {
            **defaults,
            **criteria,
        }

        opportunities: list[dict[str, Any]] = []
        collected_ids: set[str] = set()

        start_record_num = 0

        for page_number in range(
            1,
            self.max_pages_per_search + 1,
        ):
            payload = {
                "rows": self.rows_per_page,
                "startRecordNum": start_record_num,
                "keyword": "",
                "oppNum": "",
                "eligibilities": "",
                "agencies": "",
                "oppStatuses": "forecasted|posted",
                "aln": "",
                "fundingCategories": "",
            }

            # Defaults are applied first. Search-specific criteria can
            # then override them.
            payload.update(search_parameters)

            # Pagination is controlled by the client
            payload["rows"] = self.rows_per_page
            payload["startRecordNum"] = start_record_num

            LOGGER.info(
                "Searching %r, page %d, startRecordNum=%d, criteria=%s",
                search_name,
                page_number,
                start_record_num,
                criteria,
            )

            body = self._post_with_retries(payload)
            data = self._validate_response(body)

            raw_hits = data["oppHits"]
            hit_count = data["hitCount"]
            returned_start_record = data.get("startRecord")

            LOGGER.debug(
                "Grants.gov returned %d records; hitCount=%d; startRecord=%r.",
                len(raw_hits),
                hit_count,
                returned_start_record,
            )

            if not raw_hits:
                break

            new_ids_on_page = 0

            for raw_opportunity in raw_hits:
                opportunity = self._validate_opportunity(raw_opportunity)

                opportunity_id = str(opportunity["id"])

                if opportunity_id in collected_ids:
                    continue

                collected_ids.add(opportunity_id)
                opportunities.append(opportunity)
                new_ids_on_page += 1

            # Move forward by the number of results actually returned,
            # rather than assuming every page is completely full.
            start_record_num += len(raw_hits)

            # hitCount tells us the total number of matching records.
            if start_record_num >= hit_count:
                break

            # If the service returns records but none has a new ID, it may
            # have ignored our pagination offset and repeated a page.
            if new_ids_on_page == 0:
                LOGGER.warning(
                    "Search %r returned no new opportunity IDs on "
                    "page %d. Stopping to avoid a pagination loop.",
                    search_name,
                    page_number,
                )
                break
        else:
            LOGGER.warning(
                "Search %r reached the configured page limit of %d. "
                "The API reported %d total matching records, and "
                "Grant Getter collected %d.",
                search_name,
                self.max_pages_per_search,
                hit_count,
                len(opportunities),
            )

        return opportunities
