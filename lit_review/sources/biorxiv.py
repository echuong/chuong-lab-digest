"""bioRxiv preprint source via the public REST API."""
from __future__ import annotations

import time
from datetime import date
from typing import List, Optional

import requests
from dateutil import parser as dateparser

from ..models import Paper


BASE_URL = "https://api.biorxiv.org/details"
UA = "LitReviewBot/1.0 (Chuong Lab, CU Boulder; research use only)"

# Safety cap on records walked for one date range, so a very wide --days cannot
# page forever. bioRxiv posts roughly 3-4k preprints per fortnight across all
# categories, so this comfortably covers the 15-day digest window.
MAX_SCAN = 8000

# bioRxiv serves a fixed 30 records per page.
PAGE_SIZE = 30

# Give up only when this many pages in a row fail — the API is flaky enough that
# isolated pages fail routinely, and one such page must not end the scan.
MAX_CONSECUTIVE_FAILURES = 8

# Wall-clock ceiling for one scan. Retrying a flaky API is otherwise unbounded:
# a 2,793-record window that took 531s on a good day took 6,135s on a bad one,
# which is not acceptable for an unattended scheduled run. Hitting the budget is
# reported as an incomplete scan, exactly like a truncated one.
MAX_SCAN_SECONDS = 1500


def _reported_total(data: dict) -> Optional[int]:
    """
    Total records the API says match this date range.

    Paging must be driven off this rather than an assumed page size: bioRxiv
    serves 30 records per page, and any code that stops when a page comes back
    shorter than some hardcoded width silently quits after page one.
    """
    try:
        return int(data["messages"][0]["total"])
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _parse_biorxiv_paper(item: dict) -> Optional[Paper]:
    """Convert a bioRxiv API result dict to a Paper."""
    doi = item.get("doi", "").strip()
    title = item.get("title", "").strip()
    if not title or not doi:
        return None

    authors_str = item.get("authors", "")
    authors = [a.strip() for a in authors_str.split(";") if a.strip()] if authors_str else []

    date_str = item.get("date", "")
    try:
        pub_date = dateparser.parse(date_str).date()
    except Exception:
        pub_date = date.today()

    abstract = item.get("abstract", "").strip()
    category = item.get("category", "")
    url = f"https://www.biorxiv.org/content/{doi}"

    return Paper(
        doi=doi,
        title=title,
        authors=authors,
        journal=f"bioRxiv ({category})" if category else "bioRxiv",
        pub_date=pub_date,
        abstract=abstract,
        source="biorxiv",
        url=url,
    )


class BioRxivSource:
    """Fetches preprints from the bioRxiv public REST API."""

    def __init__(
        self,
        request_delay: float = 0.5,
        max_scan: int = MAX_SCAN,
        max_seconds: float = MAX_SCAN_SECONDS,
    ):
        self._delay = request_delay
        self._max_scan = max_scan
        self._max_seconds = max_seconds
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": UA})
        # Coverage of the most recent scan. A partial scan still yields a usable
        # digest, but it must be visible downstream instead of only printed:
        # one read timeout silently cut a 2,945-record window down to 1,830.
        self.last_scanned = 0
        self.last_total: Optional[int] = None
        self.last_truncated = False
        self.last_skipped_pages = 0

    def _get_page(self, url: str, attempts: int = 4, verbose: bool = True) -> Optional[dict]:
        """
        GET one page of results, retrying transient network failures.

        A single dropped connection partway through pagination would otherwise
        truncate the whole scan and look like a quiet fortnight, so a few
        retries here protect coverage. Returns None once attempts are spent.
        """
        for attempt in range(1, attempts + 1):
            try:
                resp = self._session.get(url, timeout=25)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                if attempt == attempts:
                    if verbose:
                        print(f"ERROR: {e}")
                    return None
                time.sleep(self._delay * 2 * attempt)
        return None

    def search(
        self,
        since: date,
        until: date,
        category: Optional[str] = None,
        max_results: int = 500,
    ) -> List[Paper]:
        """
        Fetch preprints in a date range.
        If category is given, filter client-side (the API does not filter by it).
        """
        date_from = since.strftime("%Y-%m-%d")
        date_to = until.strftime("%Y-%m-%d")

        papers: List[Paper] = []
        cursor = 0
        total: Optional[int] = None

        while len(papers) < max_results and cursor < self._max_scan:
            url = f"{BASE_URL}/biorxiv/{date_from}/{date_to}/{cursor}/json"

            data = self._get_page(url)
            if data is None:
                break

            collection = data.get("collection", [])
            if not collection:
                break

            if total is None:
                total = _reported_total(data)

            for item in collection:
                # Category filter (if specified)
                if category:
                    item_cat = item.get("category", "").lower().replace(" ", "_")
                    if item_cat != category.lower().replace(" ", "_"):
                        continue
                paper = _parse_biorxiv_paper(item)
                if paper:
                    papers.append(paper)

            cursor += len(collection)
            if total is not None and cursor >= total:
                break

            time.sleep(self._delay)

        return papers[:max_results]

    def search_multi_category(
        self,
        since: date,
        until: date,
        categories: List[str],
        max_per_category: int = 200,
        verbose: bool = True,
    ) -> List[Paper]:
        """
        Search across multiple bioRxiv categories and deduplicate by DOI.
        Fetches all papers in the date range, then filters client-side by category.
        """
        if verbose:
            print(f"  Fetching bioRxiv ({since} to {until})...", flush=True)

        # Fetch all papers once (more efficient than per-category calls)
        date_from = since.strftime("%Y-%m-%d")
        date_to = until.strftime("%Y-%m-%d")

        all_papers: dict[str, Paper] = {}
        wanted = [c.lower().replace(" ", "_") for c in categories]
        cursor = 0          # paging position
        seen = 0            # records actually retrieved and parsed
        skipped_pages = 0
        consecutive_failures = 0
        total: Optional[int] = None
        stopped_early = False
        started = time.monotonic()
        self.last_scanned = 0
        self.last_total = None
        self.last_truncated = False
        self.last_skipped_pages = 0

        while cursor < self._max_scan:
            if time.monotonic() - started > self._max_seconds:
                if verbose:
                    print(
                        f"    scan budget of {self._max_seconds:.0f}s reached at "
                        f"{cursor} records — stopping",
                        flush=True,
                    )
                stopped_early = True
                break

            url = f"{BASE_URL}/biorxiv/{date_from}/{date_to}/{cursor}/json"

            data = self._get_page(url, verbose=verbose)
            if data is None:
                # Skip this page and keep walking rather than abandoning the
                # scan. Giving up on the first exhausted page cost 1,844 of
                # 2,954 records on 2026-08-19 -- losing one 30-record page is
                # vastly better than losing the rest of the fortnight. Only a
                # sustained run of failures means the API is actually down.
                consecutive_failures += 1
                skipped_pages += 1
                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    if verbose:
                        print(
                            f"    bioRxiv unreachable for {consecutive_failures} "
                            f"pages in a row — stopping at {cursor}",
                            flush=True,
                        )
                    stopped_early = True
                    break
                cursor += PAGE_SIZE
                time.sleep(self._delay * 2)
                continue

            consecutive_failures = 0
            collection = data.get("collection", [])
            if not collection:
                break

            if total is None:
                total = _reported_total(data)

            for item in collection:
                item_cat = item.get("category", "").lower().replace(" ", "_")
                if not wanted or item_cat in wanted:
                    paper = _parse_biorxiv_paper(item)
                    if paper and paper.doi not in all_papers:
                        all_papers[paper.doi] = paper

            cursor += len(collection)
            seen += len(collection)
            if total is not None and cursor >= total:
                break

            # Progress, because this walks ~100 sequential pages for a fortnight
            # and used to print nothing between start and finish -- a stalled
            # scan looked exactly like a slow one in an unattended run.
            if verbose and total and cursor % (PAGE_SIZE * 10) == 0:
                note = f", {skipped_pages} page(s) skipped" if skipped_pages else ""
                print(
                    f"    ...{cursor}/{total} records, {len(all_papers)} kept{note}",
                    flush=True,
                )

            time.sleep(self._delay)

        result = list(all_papers.values())
        self.last_scanned = seen
        self.last_total = total
        self.last_skipped_pages = skipped_pages
        # `stopped_early` and `skipped_pages` matter independently of `total`:
        # when the very first page fails, total is never learned, and testing
        # `seen < total` alone would report a scan that fetched nothing as
        # complete -- reproducing the quiet-fortnight bug this all exists to
        # prevent.
        self.last_truncated = bool(
            stopped_early
            or skipped_pages
            or (total is not None and seen < total)
            or (total is None and not result)
        )

        if verbose:
            if self.last_truncated:
                scanned = f"scanned {seen}"
                if total is not None:
                    scanned += f"/{total}"
                scanned += " — INCOMPLETE"
                if skipped_pages:
                    scanned += f", {skipped_pages} page(s) unreachable"
            else:
                # `total` is a moving target during the scan, so a complete pass
                # can exceed it. Report what was walked, not a confusing ratio.
                scanned = f"scanned {seen}, complete"
            print(f"  {len(result)} preprints ({scanned})")
        return result
