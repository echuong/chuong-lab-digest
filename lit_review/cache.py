"""SQLite-backed cache for papers and feed metadata."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import List, Optional

from .models import Paper


class CacheManager:
    """Manages a SQLite cache to avoid re-fetching and re-scoring papers."""

    def __init__(self, cache_dir: Path):
        self.db_path = cache_dir / "lit_cache.db"
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                PRAGMA journal_mode=WAL;

                CREATE TABLE IF NOT EXISTS papers (
                    doi TEXT PRIMARY KEY,
                    title TEXT,
                    authors TEXT,      -- JSON array
                    journal TEXT,
                    pub_date TEXT,     -- ISO format YYYY-MM-DD
                    abstract TEXT,
                    source TEXT,
                    url TEXT,
                    pmid TEXT,
                    keyword_score REAL DEFAULT 0,
                    llm_score REAL DEFAULT 0,
                    llm_explanation TEXT DEFAULT '',
                    fetched_at TEXT,
                    included_in_reports TEXT DEFAULT ''  -- comma-separated dates
                );

                CREATE TABLE IF NOT EXISTS feed_fetches (
                    feed_url TEXT PRIMARY KEY,
                    last_fetched TEXT,
                    etag TEXT DEFAULT '',
                    last_modified TEXT DEFAULT ''
                );

                CREATE TABLE IF NOT EXISTS pubmed_queries (
                    query_hash TEXT PRIMARY KEY,
                    query TEXT,
                    last_run TEXT,
                    result_count INTEGER DEFAULT 0
                );
            """)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def is_doi_known(self, doi: str) -> bool:
        with self._conn() as conn:
            row = conn.execute("SELECT doi FROM papers WHERE doi = ?", (doi,)).fetchone()
            return row is not None

    def get_paper(self, doi: str) -> Optional[Paper]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM papers WHERE doi = ?", (doi,)).fetchone()
            if row is None:
                return None
            return self._row_to_paper(row)

    def upsert_paper(self, paper: Paper):
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO papers
                    (doi, title, authors, journal, pub_date, abstract, source, url, pmid,
                     keyword_score, llm_score, llm_explanation, fetched_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(doi) DO UPDATE SET
                    title=excluded.title,
                    authors=excluded.authors,
                    abstract=CASE WHEN excluded.abstract != '' THEN excluded.abstract ELSE abstract END,
                    keyword_score=CASE WHEN excluded.keyword_score > 0 THEN excluded.keyword_score ELSE keyword_score END,
                    llm_score=CASE WHEN excluded.llm_score > 0 THEN excluded.llm_score ELSE llm_score END,
                    llm_explanation=CASE WHEN excluded.llm_explanation != '' THEN excluded.llm_explanation ELSE llm_explanation END,
                    pmid=CASE WHEN excluded.pmid IS NOT NULL THEN excluded.pmid ELSE pmid END,
                    fetched_at=excluded.fetched_at
            """, (
                paper.doi,
                paper.title,
                json.dumps(paper.authors),
                paper.journal,
                paper.pub_date.isoformat(),
                paper.abstract,
                paper.source,
                paper.url,
                paper.pmid,
                paper.keyword_score,
                paper.llm_score,
                paper.llm_explanation,
                now,
            ))

    def get_papers_in_daterange(self, start: date, end: date) -> List[Paper]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM papers WHERE pub_date >= ? AND pub_date <= ?",
                (start.isoformat(), end.isoformat()),
            ).fetchall()
            return [self._row_to_paper(r) for r in rows]

    def get_feed_etag(self, feed_url: str) -> tuple[str, str]:
        """Returns (etag, last_modified) for conditional HTTP GET."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT etag, last_modified FROM feed_fetches WHERE feed_url = ?",
                (feed_url,),
            ).fetchone()
            if row is None:
                return ("", "")
            return (row["etag"] or "", row["last_modified"] or "")

    def set_feed_etag(self, feed_url: str, etag: str, last_modified: str):
        now = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO feed_fetches (feed_url, last_fetched, etag, last_modified)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(feed_url) DO UPDATE SET
                    last_fetched=excluded.last_fetched,
                    etag=excluded.etag,
                    last_modified=excluded.last_modified
            """, (feed_url, now, etag, last_modified))

    def mark_included_in_report(self, doi: str, report_date: date):
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT included_in_reports FROM papers WHERE doi = ?", (doi,)
            ).fetchone()
            if existing:
                reports = existing["included_in_reports"] or ""
                dates = [d for d in reports.split(",") if d]
                date_str = report_date.isoformat()
                if date_str not in dates:
                    dates.append(date_str)
                conn.execute(
                    "UPDATE papers SET included_in_reports = ? WHERE doi = ?",
                    (",".join(dates), doi),
                )

    def was_included_in_report(self, doi: str) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT included_in_reports FROM papers WHERE doi = ?", (doi,)
            ).fetchone()
            if row is None:
                return False
            return bool(row["included_in_reports"])

    @staticmethod
    def _row_to_paper(row) -> Paper:
        try:
            authors = json.loads(row["authors"]) if row["authors"] else []
        except (json.JSONDecodeError, TypeError):
            authors = []
        try:
            pub_date = date.fromisoformat(row["pub_date"])
        except (ValueError, TypeError):
            pub_date = date.today()
        return Paper(
            doi=row["doi"],
            title=row["title"] or "",
            authors=authors,
            journal=row["journal"] or "",
            pub_date=pub_date,
            abstract=row["abstract"] or "",
            source=row["source"] or "",
            url=row["url"] or "",
            pmid=row["pmid"],
            keyword_score=row["keyword_score"] or 0.0,
            llm_score=row["llm_score"] or 0.0,
            llm_explanation=row["llm_explanation"] or "",
        )
