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

    def __init__(self, request_delay: float = 0.5):
        self._delay = request_delay
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": UA})

    def search(
        self,
        since: date,
        until: date,
        category: Optional[str] = None,
        max_results: int = 500,
    ) -> List[Paper]:
        """
        Fetch preprints in a date range.
        If category is given, filter client-side (bioRxiv API filters by category).
        """
        date_from = since.strftime("%Y-%m-%d")
        date_to = until.strftime("%Y-%m-%d")

        papers = []
        cursor = 0

        while len(papers) < max_results:
            if category:
                url = f"{BASE_URL}/biorxiv/{date_from}/{date_to}/{cursor}/json"
            else:
                url = f"{BASE_URL}/biorxiv/{date_from}/{date_to}/{cursor}/json"

            try:
                resp = self._session.get(url, timeout=20)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                print(f"    bioRxiv API error at cursor {cursor}: {e}")
                break

            collection = data.get("collection", [])
            if not collection:
                break

            for item in collection:
                # Category filter (if specified)
                if category:
                    item_cat = item.get("category", "").lower().replace(" ", "_")
                    if item_cat != category.lower().replace(" ", "_"):
                        continue
                paper = _parse_biorxiv_paper(item)
                if paper:
                    papers.append(paper)

            time.sleep(self._delay)

            # bioRxiv returns up to 100 per page
            if len(collection) < 100:
                break
            cursor += len(collection)

        return papers

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
        cursor = 0
        page_limit = 2000  # safety limit

        while cursor < page_limit:
            url = f"{BASE_URL}/biorxiv/{date_from}/{date_to}/{cursor}/json"
            try:
                resp = self._session.get(url, timeout=20)
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                if verbose:
                    print(f"ERROR: {e}")
                break

            collection = data.get("collection", [])
            if not collection:
                break

            for item in collection:
                item_cat = item.get("category", "").lower().replace(" ", "_")
                if not categories or item_cat in [c.lower().replace(" ", "_") for c in categories]:
                    paper = _parse_biorxiv_paper(item)
                    if paper and paper.doi not in all_papers:
                        all_papers[paper.doi] = paper

            time.sleep(self._delay)

            if len(collection) < 100:
                break
            cursor += len(collection)

        result = list(all_papers.values())
        if verbose:
            print(f"{len(result)} preprints")
        return result
