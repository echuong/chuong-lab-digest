"""Configuration loader for the literature review agent."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import List, Dict, Optional

import yaml


@dataclass
class JournalConfig:
    name: str
    rss_url: str
    pubmed_ta: str


@dataclass
class KeywordTier:
    weight: float
    title_multiplier: float
    terms: List[str]


@dataclass
class Config:
    days_back: int
    top_n_papers: int
    top_n_preprints: int
    top_per_journal: int
    journal_highlight_min_score: float
    keyword_score_threshold: float
    output_dir: Path
    journals: List[JournalConfig]
    biorxiv_categories: List[str]
    journal_blocklist: List[str]
    keyword_tiers: Dict[str, KeywordTier]
    ncbi_api_key: Optional[str] = None
    biorxiv_max_scan: int = 8000
    biorxiv_max_seconds: float = 1500.0

    @classmethod
    def load(cls, config_path: str = "config.yaml") -> "Config":
        path = Path(config_path)
        if not path.exists():
            # Try relative to this file's location
            path = Path(__file__).parent.parent / config_path
        with open(path) as f:
            raw = yaml.safe_load(f)

        journals = [
            JournalConfig(
                name=j["name"],
                rss_url=j["rss_url"],
                pubmed_ta=j["pubmed_ta"],
            )
            for j in raw.get("journals", [])
        ]

        keyword_tiers = {}
        for tier_name, tier_data in raw.get("keyword_tiers", {}).items():
            keyword_tiers[tier_name] = KeywordTier(
                weight=float(tier_data["weight"]),
                title_multiplier=float(tier_data.get("title_multiplier", 1.0)),
                terms=tier_data["terms"],
            )

        return cls(
            days_back=raw.get("days_back", 30),
            top_n_papers=raw.get("top_n_papers", 20),
            top_n_preprints=raw.get("top_n_preprints", 10),
            top_per_journal=raw.get("top_per_journal", 3),
            journal_highlight_min_score=float(raw.get("journal_highlight_min_score", 5.0)),
            keyword_score_threshold=raw.get("keyword_score_threshold", 3.0),
            output_dir=Path(raw.get("output_dir", "reports")),
            journals=journals,
            biorxiv_categories=raw.get("biorxiv_categories", []),
            biorxiv_max_scan=int(raw.get("biorxiv_max_scan", 8000)),
            biorxiv_max_seconds=float(raw.get("biorxiv_max_seconds", 1500)),
            journal_blocklist=raw.get("journal_blocklist", []),
            keyword_tiers=keyword_tiers,
            ncbi_api_key=os.environ.get("NCBI_API_KEY"),
        )
