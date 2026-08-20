"""RSS/Atom feed fetcher for journal TOC scraping."""
from __future__ import annotations

import hashlib
import re
import time
from datetime import date
from typing import List, Optional

import feedparser
import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparser

from ..config import JournalConfig
from ..models import JournalTOC, Paper


# User-Agent to use for all HTTP requests
UA = "LitReviewBot/1.0 (Chuong Lab, CU Boulder; research use only)"


def _clean_html(text: str) -> str:
    """Strip HTML tags from text."""
    if not text:
        return ""
    soup = BeautifulSoup(text, "lxml")
    return soup.get_text(separator=" ").strip()


def _parse_date(entry) -> date:
    """Try multiple feedparser date fields and return a date object."""
    for field in ("published_parsed", "updated_parsed", "created_parsed"):
        val = getattr(entry, field, None)
        if val:
            try:
                return date(val.tm_year, val.tm_mon, val.tm_mday)
            except (ValueError, AttributeError):
                pass
    # Fallback: try string fields
    for field in ("published", "updated", "date"):
        val = getattr(entry, field, None) or entry.get(field, "")
        if val:
            try:
                return dateparser.parse(val).date()
            except Exception:
                pass
    return date.today()


def _extract_doi(entry) -> str:
    """Extract DOI from RSS entry tags, links, or id field."""
    # Check dc:identifier or prism:doi
    for tag in ("dc_identifier", "prism_doi", "dc_identifier_doi"):
        val = getattr(entry, tag, "") or ""
        if val.startswith("10."):
            return val.strip()
        if "doi.org/" in val:
            return val.split("doi.org/")[-1].strip()

    # Check links
    for link in getattr(entry, "links", []):
        href = link.get("href", "")
        if "doi.org/" in href:
            return href.split("doi.org/")[-1].strip()

    # Check entry id / guid
    for field in ("id", "guid"):
        val = getattr(entry, field, "") or ""
        if "doi.org/" in val:
            return val.split("doi.org/")[-1].strip()
        if val.startswith("10."):
            return val.strip()

    # Try regex on the entry link
    link = getattr(entry, "link", "") or ""
    m = re.search(r"10\.\d{4,}/\S+", link)
    if m:
        return m.group(0).rstrip(".,;)")

    return ""


def _extract_abstract(entry) -> str:
    """Extract abstract from RSS entry."""
    for field in ("summary", "description", "content"):
        val = getattr(entry, field, None)
        if isinstance(val, list) and val:
            val = val[0].get("value", "")
        if val:
            cleaned = _clean_html(str(val))
            if len(cleaned) > 80:
                return cleaned
    return ""


def _entry_to_paper(entry, journal_name: str) -> Optional[Paper]:
    """Convert a feedparser entry to a Paper object."""
    title = _clean_html(getattr(entry, "title", "") or "")
    if not title or title.lower() in ("", "untitled"):
        return None

    doi = _extract_doi(entry)
    if not doi:
        # Surrogate key. Must be stable across runs, so it is a title digest --
        # Python's hash() for str is salted per process, which made the old
        # surrogate different on every run and impossible to deduplicate on.
        link = getattr(entry, "link", "")
        doi = link or "no-doi-" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:16]

    # Authors
    authors = []
    author_detail = getattr(entry, "authors", []) or []
    for a in author_detail:
        name = a.get("name", "").strip()
        if name:
            authors.append(name)
    if not authors:
        author_str = getattr(entry, "author", "") or ""
        if author_str:
            authors = [a.strip() for a in author_str.split(",") if a.strip()]

    pub_date = _parse_date(entry)
    abstract = _extract_abstract(entry)
    url = getattr(entry, "link", "") or f"https://doi.org/{doi}"

    return Paper(
        doi=doi,
        title=title,
        authors=authors,
        journal=journal_name,
        pub_date=pub_date,
        abstract=abstract,
        source="journal_rss",
        url=url,
    )


class RSSFetcher:
    """Fetches journal table-of-contents via RSS/Atom feeds."""

    def __init__(self, request_delay: float = 1.0):
        self._delay = request_delay
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": UA})

    def fetch_journal(
        self, journal_config: JournalConfig, since: date, until: date
    ) -> JournalTOC:
        """Fetch new articles from a journal RSS feed within the date range."""
        feed_url = journal_config.rss_url
        result = self._fetch_feed(feed_url)
        time.sleep(self._delay)

        if result is None:
            # Fetch failed — return an empty TOC. The PubMed supplement covers
            # this journal, and stats.rss_ok will correctly show RSS did not
            # deliver it.
            return JournalTOC(
                journal_name=journal_config.name,
                papers=[],
                fetch_date=date.today(),
                feed_url=feed_url,
            )

        # result is either raw XML string or a pre-parsed feed (from fallback)
        feed = result if hasattr(result, "entries") else feedparser.parse(result)
        papers = []
        for entry in feed.entries:
            paper = _entry_to_paper(entry, journal_config.name)
            if paper is None:
                continue
            if paper.pub_date < since or paper.pub_date > until:
                continue
            papers.append(paper)

        return JournalTOC(
            journal_name=journal_config.name,
            papers=papers,
            fetch_date=date.today(),
            feed_url=feed_url,
        )

    def fetch_all_journals(
        self,
        journal_configs: List[JournalConfig],
        since: date,
        until: date,
        verbose: bool = True,
    ) -> List[JournalTOC]:
        """Fetch all journals and return list of TOCs."""
        tocs = []
        for jc in journal_configs:
            if verbose:
                print(f"  Fetching RSS: {jc.name}...", end=" ", flush=True)
            try:
                toc = self.fetch_journal(jc, since, until)
                if verbose:
                    print(f"{len(toc.papers)} articles")
                tocs.append(toc)
            except Exception as e:
                if verbose:
                    print(f"ERROR: {e}")
                tocs.append(
                    JournalTOC(
                        journal_name=jc.name,
                        papers=[],
                        fetch_date=date.today(),
                        feed_url=jc.rss_url,
                    )
                )
        return tocs

    def _fetch_feed(self, url: str):
        """
        Fetch feed content; return None on error.

        Returns either the raw feed text or, via the fallback path, an
        already-parsed feedparser result -- `fetch_journal` sniffs for `.entries`.

        This deliberately makes no conditional request. Feeds used to be fetched
        with If-None-Match/If-Modified-Since from a cached validator, but only
        the validators were cached, never the feed bodies, so a 304 returned None
        and the journal was reported as having published nothing that fortnight.
        """
        try:
            resp = self._session.get(url, timeout=15)
            resp.raise_for_status()
            return resp.text
        except requests.RequestException:
            # On failure, let feedparser try fetching the URL directly
            try:
                feed = feedparser.parse(url)
                if feed.entries:
                    return feed  # return pre-parsed feed to avoid re-fetching
                return None
            except Exception:
                return None
