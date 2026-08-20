"""PubMed/NCBI Entrez source for journal papers and abstract enrichment."""
from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import date
from typing import List, Optional

import requests
from dateutil import parser as dateparser

from ..models import Paper


BASE_ESEARCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
BASE_EFETCH = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
UA = "LitReviewBot/1.0 (Chuong Lab; edchuong@colorado.edu)"


def _get_text(elem, tag: str, default: str = "") -> str:
    child = elem.find(tag)
    return child.text.strip() if child is not None and child.text else default


def _parse_pubmed_article(article_elem) -> Optional[Paper]:
    """Parse a PubMed XML <PubmedArticle> element into a Paper."""
    try:
        medline = article_elem.find("MedlineCitation")
        if medline is None:
            return None

        pmid_elem = medline.find("PMID")
        pmid = pmid_elem.text.strip() if pmid_elem is not None else ""

        article = medline.find("Article")
        if article is None:
            return None

        title = _get_text(article, "ArticleTitle")
        if not title:
            return None

        # Abstract
        abstract_parts = []
        abstract_elem = article.find("Abstract")
        if abstract_elem is not None:
            for at in abstract_elem.findall("AbstractText"):
                label = at.get("Label", "")
                text = (at.text or "").strip()
                if label:
                    abstract_parts.append(f"{label}: {text}")
                elif text:
                    abstract_parts.append(text)
        abstract = " ".join(abstract_parts)

        # Authors
        authors = []
        author_list = article.find("AuthorList")
        if author_list is not None:
            for author in author_list.findall("Author"):
                last = _get_text(author, "LastName")
                first = _get_text(author, "ForeName") or _get_text(author, "Initials")
                if last:
                    name = f"{last} {first}".strip() if first else last
                    authors.append(name)

        # Journal
        journal_elem = article.find("Journal")
        journal_title = ""
        if journal_elem is not None:
            journal_title = (
                _get_text(journal_elem, "Title")
                or _get_text(journal_elem, "ISOAbbreviation")
            )

        # Publication date
        pub_date = date.today()
        journal_issue = journal_elem.find("JournalIssue") if journal_elem else None
        if journal_issue is not None:
            pd = journal_issue.find("PubDate")
            if pd is not None:
                year_str = _get_text(pd, "Year")
                month_str = _get_text(pd, "Month") or "Jan"
                day_str = _get_text(pd, "Day") or "1"
                try:
                    pub_date = dateparser.parse(f"{year_str} {month_str} {day_str}").date()
                except Exception:
                    pass

        # DOI
        doi = ""
        id_list = article_elem.find(".//ArticleIdList")
        if id_list is not None:
            for aid in id_list.findall("ArticleId"):
                if aid.get("IdType") == "doi":
                    doi = (aid.text or "").strip()
                    break

        if not doi and pmid:
            doi = f"pmid:{pmid}"

        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/" if pmid else f"https://doi.org/{doi}"

        return Paper(
            doi=doi,
            title=title,
            authors=authors,
            journal=journal_title,
            pub_date=pub_date,
            abstract=abstract,
            source="pubmed",
            url=url,
            pmid=pmid,
        )
    except Exception:
        return None


class PubMedSource:
    """Queries PubMed via NCBI Entrez API for journal papers."""

    def __init__(self, api_key: Optional[str] = None, request_delay: float = 0.4):
        self._api_key = api_key
        self._delay = 0.15 if api_key else request_delay
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": UA})

    def _esearch(self, query: str, max_results: int = 200) -> List[str]:
        """Run an esearch query and return a list of PMIDs."""
        params = {
            "db": "pubmed",
            "term": query,
            "retmax": max_results,
            "retmode": "json",
            "usehistory": "n",
        }
        if self._api_key:
            params["api_key"] = self._api_key

        try:
            resp = self._session.get(BASE_ESEARCH, params=params, timeout=20)
            resp.raise_for_status()
            data = resp.json()
            pmids = data.get("esearchresult", {}).get("idlist", [])
            time.sleep(self._delay)
            return pmids
        except Exception as e:
            print(f"    PubMed esearch error: {e}")
            return []

    def _efetch(self, pmids: List[str], attempts: int = 3) -> List[Paper]:
        """
        Fetch full records for a list of PMIDs, in batches of 100.

        Each batch is retried on failure: NCBI returns intermittent 502s, and a
        dropped batch silently costs up to 100 papers from that journal. One 502
        cost Science Advances 17 papers in a 2026-08-19 run.
        """
        if not pmids:
            return []

        papers = []
        batch_size = 100
        for i in range(0, len(pmids), batch_size):
            batch = pmids[i : i + batch_size]
            params = {
                "db": "pubmed",
                "id": ",".join(batch),
                "rettype": "xml",
                "retmode": "xml",
            }
            if self._api_key:
                params["api_key"] = self._api_key

            for attempt in range(1, attempts + 1):
                try:
                    resp = self._session.post(BASE_EFETCH, data=params, timeout=30)
                    resp.raise_for_status()
                    root = ET.fromstring(resp.content)
                    for article_elem in root.findall("PubmedArticle"):
                        paper = _parse_pubmed_article(article_elem)
                        if paper:
                            papers.append(paper)
                    time.sleep(self._delay)
                    break
                except Exception as e:
                    if attempt == attempts:
                        print(f"    PubMed efetch FAILED (batch {i}, {len(batch)} papers lost): {e}")
                    else:
                        time.sleep(self._delay * 2 * attempt)

        return papers

    def _build_date_filter(self, since: date, until: date) -> str:
        return f'("{since.strftime("%Y/%m/%d")}"[PDAT]:"{until.strftime("%Y/%m/%d")}"[PDAT])'

    def search_journal(
        self, journal_ta: str, since: date, until: date, max_results: int = 200
    ) -> List[Paper]:
        """Fetch all papers from a specific journal in a date range."""
        date_filter = self._build_date_filter(since, until)
        query = f'"{journal_ta}"[TA] AND {date_filter}'
        pmids = self._esearch(query, max_results)
        return self._efetch(pmids)

    def search_topic(
        self, topic_query: str, since: date, until: date, max_results: int = 200
    ) -> List[Paper]:
        """Search PubMed by topic keywords in a date range."""
        date_filter = self._build_date_filter(since, until)
        query = f"({topic_query}) AND {date_filter}"
        pmids = self._esearch(query, max_results)
        return self._efetch(pmids)

    def enrich_abstracts(self, papers: List[Paper]) -> List[Paper]:
        """
        For papers missing abstracts, try to fetch them from PubMed by DOI.
        Returns the same list with abstracts filled in where possible.
        """
        need_abstract = [p for p in papers if not p.abstract and p.doi and not p.doi.startswith("no-doi")]
        if not need_abstract:
            return papers

        # Build DOI -> paper index
        doi_map = {p.doi: p for p in need_abstract}

        # Search PubMed by DOI
        doi_queries = [f'"{doi}"[DOI]' for doi in doi_map]
        # Batch into groups of 20
        for i in range(0, len(doi_queries), 20):
            batch = doi_queries[i : i + 20]
            query = " OR ".join(batch)
            pmids = self._esearch(query, max_results=len(batch))
            fetched = self._efetch(pmids)
            for fp in fetched:
                # Match back by DOI
                for orig_doi in doi_map:
                    if fp.doi == orig_doi or (fp.pmid and f"pmid:{fp.pmid}" == orig_doi):
                        if fp.abstract:
                            doi_map[orig_doi].abstract = fp.abstract
                        if fp.pmid:
                            doi_map[orig_doi].pmid = fp.pmid

        return papers

