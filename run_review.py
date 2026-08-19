#!/usr/bin/env python3
"""
Chuong Lab Weekly Literature Review Agent
==========================================
Usage:
    python run_review.py                     # last 30 days, keyword scoring
    python run_review.py --days 14           # custom date range
    python run_review.py --journals-only     # skip bioRxiv
    python run_review.py --preprints-only    # skip journal RSS
    python run_review.py --output /path/dir  # custom output directory
    python run_review.py --dry-run           # print stats, don't save report

    # Apply AI enrichments from Claude Code (reads JSON from stdin):
    python run_review.py --enrich reports/digest_2026-03-22.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List

# Allow running from the project root
sys.path.insert(0, str(Path(__file__).parent))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from lit_review.cache import CacheManager
from lit_review.config import Config
from lit_review.models import DigestReport, DigestStats, JournalTOC, Paper
from lit_review.ranking.keyword_scorer import KeywordScorer
from lit_review.report.formatter import ReportFormatter
from lit_review.sources.biorxiv import BioRxivSource
from lit_review.sources.pubmed import PubMedSource
from lit_review.sources.rss_fetcher import RSSFetcher

console = Console()


def deduplicate(papers: List[Paper]) -> List[Paper]:
    """Remove duplicate papers by DOI, preserving order."""
    seen = set()
    result = []
    for p in papers:
        key = p.doi.lower().strip()
        if not key or key.startswith("no-doi"):
            result.append(p)
        elif key not in seen:
            seen.add(key)
            result.append(p)
    return result


def merge_into_tocs(
    tocs: List[JournalTOC],
    pubmed_by_journal: Dict[str, List[Paper]],
) -> List[JournalTOC]:
    """
    Merge PubMed papers into existing TOC objects.
    For journals with no RSS papers, replace with PubMed papers.
    For journals with RSS papers, supplement with any PubMed papers not already present.
    """
    updated = []
    for toc in tocs:
        existing_dois = {p.doi.lower() for p in toc.papers if not p.doi.startswith("no-doi")}
        supplement = pubmed_by_journal.get(toc.journal_name, [])
        new_papers = [p for p in supplement if p.doi.lower() not in existing_dois]
        merged_papers = deduplicate(toc.papers + new_papers)
        updated.append(JournalTOC(
            journal_name=toc.journal_name,
            papers=merged_papers,
            fetch_date=toc.fetch_date,
            total_items=len(merged_papers),
            feed_url=toc.feed_url,
        ))
    return updated


def is_blocked(paper: Paper, blocklist: List[str]) -> bool:
    """Return True if the paper's journal matches any blocklist entry."""
    journal_lower = paper.journal.lower()
    for blocked in blocklist:
        if blocked.lower() in journal_lower:
            return True
    return False


def pick_journal_highlights(
    tocs: List[JournalTOC],
    scorer: KeywordScorer,
    top_per_journal: int = 3,
    min_score: float = 5.0,
) -> List[Paper]:
    """
    From each journal's TOC, pick the top-scored papers as highlights.
    Ensures journal diversity by taking top_per_journal per journal.
    Returns a flat list deduplicated by DOI, sorted by score desc.
    """
    highlights = []
    seen: set = set()
    for toc in tocs:
        if not toc.papers:
            continue
        for p in toc.papers:
            if p.keyword_score == 0.0:
                p.keyword_score = scorer.score(p)
        best = sorted(
            [p for p in toc.papers if p.keyword_score >= min_score],
            key=lambda p: p.keyword_score,
            reverse=True,
        )[:top_per_journal]
        for p in best:
            if p.doi not in seen:
                seen.add(p.doi)
                highlights.append(p)
    return sorted(highlights, key=lambda p: p.keyword_score, reverse=True)


def merge_top_papers_with_highlights(
    top_papers: List[Paper],
    journal_highlights: List[Paper],
    tracked_journals: List[str],
    max_total: int = 20,
) -> List[Paper]:
    """
    Merge the top keyword-scored papers with journal highlights to get a
    diverse combined list. Ensures all tracked journals with relevant content
    are represented. Deduplicates by DOI and caps at max_total.
    Also filters papers to only include those from tracked journals.
    """
    tracked_lower = {j.lower() for j in tracked_journals}

    def is_tracked(paper: Paper) -> bool:
        return any(paper.journal.lower() == j for j in tracked_lower)

    seen: set = set()
    result: List[Paper] = []

    # First pass: add all top_papers that are from tracked journals
    for p in top_papers:
        if p.doi not in seen and is_tracked(p):
            seen.add(p.doi)
            result.append(p)

    # Second pass: add highlights not already in result
    for p in journal_highlights:
        if p.doi not in seen and is_tracked(p):
            seen.add(p.doi)
            result.append(p)

    # Re-sort by keyword score and cap
    result.sort(key=lambda p: p.keyword_score, reverse=True)
    return result[:max_total]


def _heuristic_finding(paper: Paper) -> str:
    """
    Generate a heuristic one-sentence finding based on keyword matches.
    Used as fallback when AI enrichment is not available.
    """
    title = paper.title.lower()
    abstract = paper.abstract.lower()
    text = title + " " + abstract

    if any(t in text for t in ["transposable element", "endogenous retrovirus", "retrotransposon", "sine", "line-1", "alu element", "ltr"]):
        if any(t in text for t in ["interferon", "innate immune", "ifn"]):
            return "TE-derived regulatory elements in immune signaling."
        if any(t in text for t in ["cancer", "tumor", "colorectal", "ovarian"]):
            return "TE reactivation in cancer context."
        return "Transposable element biology."
    if any(t in text for t in ["ifnar2", "ifnar1"]):
        return "IFNAR biology."
    if "exonization" in text or ("alternative splicing" in text and "immune" in text):
        return "TE exonization or immune splicing."
    if any(t in text for t in ["enhancer", "crispr", "atac-seq", "chip-seq"]) and any(t in text for t in ["immune", "interferon", "ifn", "inflammation"]):
        return "Functional genomics of immune regulation."
    if "long-read" in text or "nanopore" in text:
        return "Long-read transcriptomics approach."
    if "pan-genome" in text or "structural variant" in text:
        return "Pan-genome or SV methods."
    return ""


def enrich_report(digest_path: str) -> None:
    """
    Apply AI enrichments to an existing digest JSON.
    Reads enrichment data from stdin as JSON with format:
    {
        "topic_groups": {"TE Biology": ["doi1", ...], ...},
        "findings": {"doi1": "one-sentence finding", ...},
        "summaries": {"doi1": "2-4 sentence summary", ...}
    }
    Re-renders HTML and Markdown reports.
    """
    json_path = Path(digest_path)
    if not json_path.exists():
        console.print(f"[red]Error: digest JSON not found: {json_path}[/]")
        sys.exit(1)

    console.print(f"[bold]Loading digest from {json_path}...[/]")
    report = DigestReport.from_json(str(json_path))

    console.print("[bold]Reading enrichments from stdin...[/]")
    enrichments = json.load(sys.stdin)

    # Build DOI → Paper index across all paper lists
    doi_index: Dict[str, Paper] = {}
    for p in report.top_papers + report.top_preprints:
        doi_index[p.doi] = p

    # Apply executive summary
    if enrichments.get("executive_summary"):
        report.stats.executive_summary = enrichments["executive_summary"]
        console.print("  Applied executive summary")

    # Apply findings (one-sentence key result for inline display)
    if enrichments.get("findings"):
        count = 0
        for doi, finding in enrichments["findings"].items():
            if doi in doi_index:
                doi_index[doi].finding = finding
                count += 1
        console.print(f"  Applied {count} findings")

    # Apply summaries (3-5 sentence detail pane summary)
    if enrichments.get("summaries"):
        count = 0
        for doi, summary in enrichments["summaries"].items():
            if doi in doi_index:
                doi_index[doi].summary = summary
                count += 1
        console.print(f"  Applied {count} summaries")

    # Apply per-journal mini-summaries (journal name → 1-2 sentence summary)
    if enrichments.get("journal_summaries"):
        report.stats.journal_summaries = enrichments["journal_summaries"]
        console.print(f"  Applied {len(enrichments['journal_summaries'])} journal summaries")

    # Apply topic groups. Use freshly-provided groups if present, otherwise
    # re-resolve from any groups already stored on the report so that a partial
    # re-enrichment (e.g. adding only journal summaries) doesn't drop them.
    source_groups = enrichments.get("topic_groups") or report.stats.topic_groups
    if source_groups:
        report.stats.topic_groups = source_groups
        # Resolve DOIs to Paper objects for template rendering
        resolved = {}
        for topic, dois in source_groups.items():
            papers = [doi_index[d] for d in dois if d in doi_index]
            if papers:
                resolved[topic] = papers
        report.stats.topic_groups_resolved = resolved
        report.stats.scoring_mode = "keyword + AI"
        console.print(f"  Applied {len(resolved)} topic groups")

    # Re-save JSON, HTML, and MD
    report.to_json(str(json_path))
    output_dir = json_path.parent
    formatter = ReportFormatter()
    html_path, md_path = formatter.save(report, output_dir)
    console.print(f"\n[bold green]✓ Enriched report saved:[/]")
    console.print(f"  HTML: [link={html_path}]{html_path}[/link]")
    console.print(f"  MD:   {md_path}")


def run_digest(
    config: Config,
    days: int,
    skip_journals: bool,
    skip_biorxiv: bool,
    dry_run: bool,
    output_dir: Path,
) -> None:
    today = date.today()
    period_start = today - timedelta(days=days)
    period_end = today

    console.print(Panel.fit(
        f"[bold cyan]Chuong Lab Literature Digest[/]\n"
        f"Period: [yellow]{period_start}[/] → [yellow]{period_end}[/] ({days} days)\n"
        f"Scoring: [green]keyword[/]",
        border_style="cyan"
    ))

    output_dir.mkdir(parents=True, exist_ok=True)
    config.cache_dir.mkdir(parents=True, exist_ok=True)

    cache = CacheManager(config.cache_dir)
    scorer = KeywordScorer(config)
    formatter = ReportFormatter()

    all_journal_papers: List[Paper] = []
    journal_tocs: List[JournalTOC] = []
    pubmed = PubMedSource(api_key=config.ncbi_api_key)

    # ── Step 1: Fetch journal RSS feeds ──────────────────────────────────────
    if not skip_journals:
        console.print("\n[bold]Step 1/3: Fetching journal content...[/]")

        # 1a. RSS feeds
        console.print("  [dim]Fetching RSS feeds...[/]")
        etag_cache = {jc.rss_url: cache.get_feed_etag(jc.rss_url) for jc in config.journals}
        rss = RSSFetcher(cache_etags=etag_cache, request_delay=0.8)
        tocs = rss.fetch_all_journals(config.journals, period_start, period_end, verbose=True)
        for url, (etag, lm) in rss._etags.items():
            cache.set_feed_etag(url, etag, lm)

        rss_total = sum(len(t.papers) for t in tocs)
        console.print(f"  → [green]{rss_total}[/] articles from RSS")

        # 1b. PubMed supplement — fetch all 20 journals via Entrez
        console.print("  [dim]Supplementing with PubMed...[/]")
        pubmed_by_journal: Dict[str, List[Paper]] = {}
        for jc in config.journals:
            try:
                papers = pubmed.search_journal(jc.pubmed_ta, period_start, period_end, max_results=100)
                for p in papers:
                    p.journal = jc.name
                pubmed_by_journal[jc.name] = papers
                console.print(f"    PubMed: {jc.name}... {len(papers)}")
            except Exception as e:
                console.print(f"[yellow]err: {e}[/]")
                pubmed_by_journal[jc.name] = []

        # 1c. Merge RSS + PubMed into TOCs
        journal_tocs = merge_into_tocs(tocs, pubmed_by_journal)
        merged_total = sum(len(t.papers) for t in journal_tocs)
        console.print(f"  → [green]{merged_total}[/] total after merge")

        # Flatten all journal papers and filter blocklist
        all_rss_pubmed: List[Paper] = []
        for toc in journal_tocs:
            all_rss_pubmed.extend(toc.papers)
        all_rss_pubmed = [p for p in all_rss_pubmed if not is_blocked(p, config.journal_blocklist)]
        all_journal_papers = deduplicate(all_rss_pubmed)
        console.print(f"  → [green]{len(all_journal_papers)}[/] unique articles after dedup + blocklist filter")

        # 1d. Enrich missing abstracts via PubMed DOI lookup
        missing_before = sum(1 for p in all_journal_papers if not p.abstract and p.doi and not p.doi.startswith("no-doi"))
        if missing_before > 0:
            console.print(f"  [dim]Enriching abstracts for {missing_before} papers via PubMed...[/]")
            pubmed.enrich_abstracts(all_journal_papers)
            missing_after = sum(1 for p in all_journal_papers if not p.abstract and p.doi and not p.doi.startswith("no-doi"))
            enriched = missing_before - missing_after
            console.print(f"  → [green]{enriched}[/] abstracts enriched ({missing_after} still missing)")

    # ── Step 2: Fetch bioRxiv preprints ──────────────────────────────────────
    all_preprints: List[Paper] = []
    if not skip_biorxiv:
        console.print("\n[bold]Step 2/3: Fetching bioRxiv preprints...[/]")
        biorxiv = BioRxivSource(request_delay=0.5)
        preprints = biorxiv.search_multi_category(
            since=period_start,
            until=period_end,
            categories=config.biorxiv_categories,
            verbose=True,
        )
        all_preprints = deduplicate(preprints)
        console.print(f"  → [green]{len(all_preprints)}[/] preprints after dedup")

    # ── Step 3: Relevance scoring ─────────────────────────────────────────────
    console.print("\n[bold]Step 3/3: Scoring papers for relevance...[/]")

    scorer.score_batch(all_journal_papers)
    scorer.score_batch(all_preprints)

    scored_journal = sorted(
        [p for p in all_journal_papers if p.keyword_score >= config.keyword_score_threshold],
        key=lambda p: p.keyword_score, reverse=True,
    )
    scored_preprints = sorted(
        [p for p in all_preprints if p.keyword_score >= config.keyword_score_threshold],
        key=lambda p: p.keyword_score, reverse=True,
    )
    console.print(f"  Journal: {len(scored_journal)}/{len(all_journal_papers)} above threshold (≥ {config.keyword_score_threshold})")
    console.print(f"  Preprints: {len(scored_preprints)}/{len(all_preprints)} above threshold")

    # ── Merge top_papers with journal highlights into one diverse list ────────
    raw_top = scored_journal[:config.top_n_papers * 3]

    journal_highlights = pick_journal_highlights(
        journal_tocs, scorer, top_per_journal=3, min_score=5.0
    )

    tracked_journal_names = [jc.name for jc in config.journals]

    top_papers = merge_top_papers_with_highlights(
        raw_top, journal_highlights,
        tracked_journals=tracked_journal_names,
        max_total=config.top_n_papers * 2,
    )
    top_preprints = scored_preprints[:config.top_n_preprints]
    console.print(f"  Combined top papers (tracked journals, diverse): {len(top_papers)}")
    console.print(f"  Top preprints: {len(top_preprints)}")

    # ── Heuristic findings (fallback until AI enrichment) ──────────────────
    for p in top_papers + top_preprints:
        p.finding = _heuristic_finding(p)

    # ── Build RSS vs PubMed status ────────────────────────────────────────────
    RSS_BROKEN = {"Molecular Biology and Evolution"}
    rss_ok_list = [jc.name for jc in config.journals if jc.name not in RSS_BROKEN]
    pubmed_only_list = list(RSS_BROKEN)

    # ── Build stats ───────────────────────────────────────────────────────────
    journals_with_content = sum(1 for t in journal_tocs if t.papers)
    stats = DigestStats(
        total_journal_papers=len(all_journal_papers),
        total_preprints=len(all_preprints),
        total_papers_scanned=len(all_journal_papers) + len(all_preprints),
        journals_checked=len(journal_tocs),
        journals_with_content=journals_with_content,
        date_range_days=days,
        scoring_mode="keyword",
        rss_ok=rss_ok_list,
        pubmed_only=pubmed_only_list,
    )

    report = DigestReport(
        generated_date=today,
        period_start=period_start,
        period_end=period_end,
        top_papers=top_papers,
        top_preprints=top_preprints,
        journal_tocs=journal_tocs,
        stats=stats,
        all_journal_papers=all_journal_papers,
        all_preprints=all_preprints,
    )

    # ── Cache all papers ──────────────────────────────────────────────────────
    for p in all_journal_papers + all_preprints:
        cache.upsert_paper(p)
    for p in top_papers + top_preprints:
        cache.mark_included_in_report(p.doi, today)

    # ── Print summary ─────────────────────────────────────────────────────────
    console.print("\n[bold green]═══ Digest Summary ═══[/]")
    t = Table(show_header=False, box=None, padding=(0, 1))
    t.add_row("Papers scanned:", f"[cyan]{stats.total_papers_scanned}[/]")
    t.add_row("Preprints:", f"[cyan]{stats.total_preprints}[/]")
    t.add_row("Journals with content:", f"[cyan]{stats.journals_with_content}/{stats.journals_checked}[/]")
    t.add_row("Top journal papers:", f"[green]{len(top_papers)}[/]")
    t.add_row("Top preprints:", f"[green]{len(top_preprints)}[/]")
    console.print(t)

    if dry_run:
        console.print("\n[yellow]Dry run — no report saved.[/]")
        return

    # Save JSON for Claude Code enrichment
    json_path = output_dir / f"digest_{today.strftime('%Y-%m-%d')}.json"
    report.to_json(str(json_path))

    html_path, md_path = formatter.save(report, output_dir)
    console.print(f"\n[bold green]✓ Report saved:[/]")
    console.print(f"  JSON: {json_path}")
    console.print(f"  HTML: [link={html_path}]{html_path}[/link]")
    console.print(f"  MD:   {md_path}")
    console.print(f"\n[dim]Enrich with AI: use /digest skill in Claude Code[/]")


def main():
    parser = argparse.ArgumentParser(
        description="Chuong Lab weekly literature review digest",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--days", type=int, default=None,
                        help="Days back to search (default: from config.yaml, usually 30)")
    parser.add_argument("--journals-only", action="store_true",
                        help="Skip bioRxiv, fetch journal RSS only")
    parser.add_argument("--preprints-only", action="store_true",
                        help="Skip journal RSS, fetch bioRxiv only")
    parser.add_argument("--output", type=str, default=None,
                        help="Output directory for reports (default: from config.yaml)")
    parser.add_argument("--config", type=str, default="config.yaml",
                        help="Path to config.yaml")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch and score but don't save the report")
    parser.add_argument("--enrich", type=str, default=None, metavar="JSON_PATH",
                        help="Apply AI enrichments to an existing digest JSON (reads from stdin)")
    args = parser.parse_args()

    # Enrich mode: apply enrichments to existing digest
    if args.enrich:
        enrich_report(args.enrich)
        return

    try:
        config = Config.load(args.config)
    except FileNotFoundError:
        console.print(f"[red]Error: config file not found: {args.config}[/]")
        sys.exit(1)

    days = args.days if args.days is not None else config.days_back
    output_dir = Path(args.output) if args.output else config.output_dir

    start_time = time.time()
    run_digest(
        config=config,
        days=days,
        skip_journals=args.preprints_only,
        skip_biorxiv=args.journals_only,
        dry_run=args.dry_run,
        output_dir=output_dir,
    )
    elapsed = time.time() - start_time
    console.print(f"\n[dim]Completed in {elapsed:.1f}s[/]")


if __name__ == "__main__":
    main()
