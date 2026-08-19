"""Data models for the literature review agent."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from typing import List, Dict, Optional


@dataclass
class Paper:
    """Represents a single paper or preprint."""
    doi: str
    title: str
    authors: List[str]
    journal: str
    pub_date: date
    abstract: str
    source: str  # "journal_rss", "pubmed", "biorxiv"
    url: str
    pmid: Optional[str] = None
    keyword_score: float = 0.0
    llm_score: float = 0.0
    llm_explanation: str = ""
    lay_summary: str = ""  # kept for backward compat
    finding: str = ""      # one-sentence key result (inline display)
    summary: str = ""      # 2-4 sentence layman summary (detail pane)

    @property
    def is_preprint(self) -> bool:
        return self.source == "biorxiv" or "biorxiv" in self.journal.lower()

    @property
    def short_authors(self) -> str:
        if len(self.authors) == 0:
            return "Unknown"
        if len(self.authors) == 1:
            return self.authors[0]
        if len(self.authors) <= 3:
            return ", ".join(self.authors)
        return f"{self.authors[0]} et al."

    @property
    def abbrev_authors(self) -> str:
        """First + last author for compact display (when >4 authors)."""
        if len(self.authors) == 0:
            return "Unknown"
        if len(self.authors) == 1:
            return self.authors[0]
        if len(self.authors) <= 4:
            return ", ".join(self.authors)
        return f"{self.authors[0]}, {self.authors[-1]} et al."

    @property
    def display_score(self) -> float:
        """LLM score if available, else normalized keyword score."""
        if self.llm_score > 0:
            return self.llm_score
        # Normalize keyword score to 0-10 scale (keyword scores can go up to ~100+)
        return min(10.0, self.keyword_score / 10.0)

    def to_dict(self) -> dict:
        return {
            "doi": self.doi,
            "title": self.title,
            "authors": self.authors,
            "journal": self.journal,
            "pub_date": self.pub_date.isoformat(),
            "abstract": self.abstract,
            "source": self.source,
            "url": self.url,
            "pmid": self.pmid,
            "keyword_score": self.keyword_score,
            "llm_score": self.llm_score,
            "llm_explanation": self.llm_explanation,
            "lay_summary": self.lay_summary,
            "finding": self.finding,
            "summary": self.summary,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Paper":
        return cls(
            doi=d["doi"],
            title=d["title"],
            authors=d.get("authors", []),
            journal=d.get("journal", ""),
            pub_date=date.fromisoformat(d["pub_date"]),
            abstract=d.get("abstract", ""),
            source=d.get("source", ""),
            url=d.get("url", ""),
            pmid=d.get("pmid"),
            keyword_score=d.get("keyword_score", 0.0),
            llm_score=d.get("llm_score", 0.0),
            llm_explanation=d.get("llm_explanation", ""),
            lay_summary=d.get("lay_summary", ""),
            finding=d.get("finding", ""),
            summary=d.get("summary", ""),
        )


@dataclass
class JournalTOC:
    """Table of contents for a single journal fetch."""
    journal_name: str
    papers: List[Paper]
    fetch_date: date
    total_items: int
    feed_url: str = ""

    def to_dict(self) -> dict:
        return {
            "journal_name": self.journal_name,
            "papers": [p.to_dict() for p in self.papers],
            "fetch_date": self.fetch_date.isoformat(),
            "total_items": self.total_items,
            "feed_url": self.feed_url,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "JournalTOC":
        return cls(
            journal_name=d["journal_name"],
            papers=[Paper.from_dict(p) for p in d.get("papers", [])],
            fetch_date=date.fromisoformat(d["fetch_date"]),
            total_items=d["total_items"],
            feed_url=d.get("feed_url", ""),
        )


@dataclass
class DigestStats:
    """Statistics about the digest run."""
    total_journal_papers: int = 0
    total_preprints: int = 0
    total_papers_scanned: int = 0
    journals_checked: int = 0
    journals_with_content: int = 0
    date_range_days: int = 30
    scoring_mode: str = "keyword"
    rss_ok: List[str] = field(default_factory=list)
    pubmed_only: List[str] = field(default_factory=list)
    executive_summary: str = ""
    topic_groups: Dict[str, List[str]] = field(default_factory=dict)  # topic name → list of DOIs
    topic_groups_resolved: Dict[str, List[Paper]] = field(default_factory=dict)  # resolved for template
    journal_summaries: Dict[str, str] = field(default_factory=dict)  # journal name → 1-2 sentence AI summary

    def to_dict(self) -> dict:
        return {
            "total_journal_papers": self.total_journal_papers,
            "total_preprints": self.total_preprints,
            "total_papers_scanned": self.total_papers_scanned,
            "journals_checked": self.journals_checked,
            "journals_with_content": self.journals_with_content,
            "date_range_days": self.date_range_days,
            "scoring_mode": self.scoring_mode,
            "rss_ok": self.rss_ok,
            "pubmed_only": self.pubmed_only,
            "executive_summary": self.executive_summary,
            "topic_groups": self.topic_groups,
            "journal_summaries": self.journal_summaries,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DigestStats":
        return cls(
            total_journal_papers=d.get("total_journal_papers", 0),
            total_preprints=d.get("total_preprints", 0),
            total_papers_scanned=d.get("total_papers_scanned", 0),
            journals_checked=d.get("journals_checked", 0),
            journals_with_content=d.get("journals_with_content", 0),
            date_range_days=d.get("date_range_days", 30),
            scoring_mode=d.get("scoring_mode", "keyword"),
            rss_ok=d.get("rss_ok", []),
            pubmed_only=d.get("pubmed_only", []),
            executive_summary=d.get("executive_summary", ""),
            topic_groups=d.get("topic_groups", {}),
            journal_summaries=d.get("journal_summaries", {}),
        )


@dataclass
class DigestReport:
    """The complete weekly digest report."""
    generated_date: date
    period_start: date
    period_end: date
    top_papers: List[Paper]
    top_preprints: List[Paper]
    journal_tocs: List[JournalTOC]
    stats: DigestStats
    all_journal_papers: List[Paper] = field(default_factory=list)
    all_preprints: List[Paper] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "generated_date": self.generated_date.isoformat(),
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "top_papers": [p.to_dict() for p in self.top_papers],
            "top_preprints": [p.to_dict() for p in self.top_preprints],
            "journal_tocs": [t.to_dict() for t in self.journal_tocs],
            "stats": self.stats.to_dict(),
            "all_journal_papers": [p.to_dict() for p in self.all_journal_papers],
            "all_preprints": [p.to_dict() for p in self.all_preprints],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DigestReport":
        # Deserialize all papers into a DOI-keyed pool so duplicates share
        # the same Python object. This ensures enrichment updates propagate
        # across all lists that reference the same paper.
        paper_pool: Dict[str, Paper] = {}

        def _dedup_paper(pd: dict) -> Paper:
            p = Paper.from_dict(pd)
            if p.doi in paper_pool:
                return paper_pool[p.doi]
            paper_pool[p.doi] = p
            return p

        def _dedup_papers(lst: list) -> List[Paper]:
            return [_dedup_paper(pd) for pd in lst]

        top_papers = _dedup_papers(d.get("top_papers", []))
        top_preprints = _dedup_papers(d.get("top_preprints", []))
        all_journal_papers = _dedup_papers(d.get("all_journal_papers", []))
        all_preprints = _dedup_papers(d.get("all_preprints", []))

        # Deserialize TOCs using the same pool
        journal_tocs = []
        for td in d.get("journal_tocs", []):
            toc_papers = _dedup_papers(td.get("papers", []))
            journal_tocs.append(JournalTOC(
                journal_name=td["journal_name"],
                papers=toc_papers,
                fetch_date=date.fromisoformat(td["fetch_date"]),
                total_items=td["total_items"],
                feed_url=td.get("feed_url", ""),
            ))

        stats_d = d.get("stats", {})
        stats = DigestStats.from_dict(stats_d)

        return cls(
            generated_date=date.fromisoformat(d["generated_date"]),
            period_start=date.fromisoformat(d["period_start"]),
            period_end=date.fromisoformat(d["period_end"]),
            top_papers=top_papers,
            top_preprints=top_preprints,
            journal_tocs=journal_tocs,
            stats=stats,
            all_journal_papers=all_journal_papers,
            all_preprints=all_preprints,
        )

    def to_json(self, path: str) -> None:
        """Save report as JSON file."""
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    @classmethod
    def from_json(cls, path: str) -> "DigestReport":
        """Load report from JSON file."""
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))
