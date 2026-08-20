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

    def __init__(self, request_delay: float = 0.5, max_scan: int = MAX_SCAN):
        self._delay = request_delay
        self._max_scan = max_scan
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": UA})

    def _get_page(self, url: str, attempts: int = 3, verbose: bool = True) -> Optional[dict]:
        """
        GET one page of results, retrying transient network failures.

        A single dropped connection partway through pagination would otherwise
        truncate the whole scan and look like a quiet fortnight, so a few
        retries here protect coverage. Returns None once attempts are spent.
        """
        for attempt in range(1, attempts + 1):
            try:
                resp = self._session.get(url, timeout=20)
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
            print(f"  Fetching bioRxiv ({since} to {until})...", end=" ", flush=True)

        # Fetch all papers once (more efficient than per-category calls)
        date_from = since.strftime("%Y-%m-%d")
        date_to = until.strftime("%Y-%m-%d")

        all_papers: dict[str, Paper] = {}
        wanted = [c.lower().replace(" ", "_") for c in categories]
        cursor = 0
        total: Optional[int] = None

        while cursor < self._max_scan:
            url = f"{BASE_URL}/biorxiv/{date_from}/{date_to}/{cursor}/json"

            data = self._get_page(url, verbose=verbose)
            if data is None:
                break

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
            if total is not None and cursor >= total:
                break

            time.sleep(self._delay)

        result = list(all_papers.values())
        if verbose:
            scanned = f"scanned {cursor}"
            if total is not None:
                scanned += f"/{total}"
                if cursor < total:
                    scanned += " — TRUNCATED"
            print(f"{len(result)} preprints ({scanned})")
        return result
