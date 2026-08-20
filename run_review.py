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
import re
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

from lit_review.config import Config
from lit_review.models import DigestReport, DigestStats, JournalTOC, Paper
from lit_review.ranking.keyword_scorer import KeywordScorer
from lit_review.report.formatter import ReportFormatter
from lit_review.sources.biorxiv import BioRxivSource
from lit_review.sources.pubmed import PubMedSource
from lit_review.sources.rss_fetcher import RSSFetcher

console = Console()


def deduplicate(papers: List[Paper]) -> List[Paper]:
    """
    Remove duplicate papers by DOI, preserving order.

    Papers with no DOI carry a stable title-derived surrogate key (see
    rss_fetcher._entry_to_paper), so they deduplicate like anything else. This
    used to exempt "no-doi" keys entirely, because the surrogate was built from
    Python's per-process randomized hash() and could not be compared at all.
    """
    seen = set()
    result = []
    for p in papers:
        key = p.doi.lower().strip()
        if not key:
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
            feed_url=toc.feed_url,
        ))
    return updated


_EDITORIAL_NOTICE = re.compile(
    r"^\s*(author correction|publisher correction|editorial expression of concern"
    r"|expression of concern|retraction|retracted|erratum|correction to|addendum)\b",
    re.IGNORECASE,
)


def is_editorial_notice(paper: Paper) -> bool:
    """
    True for corrections, errata, and retraction notices.

    These are metadata records, not papers, but they inherit the title and
    abstract of the article they amend and so score just as highly. Two
    "Author Correction:" entries ranked in the top ten of the 2026-08-19 run.
    """
    return bool(_EDITORIAL_NOTICE.match(paper.title or ""))


def is_blocked(paper: Paper, blocklist: List[str], tracked_journals: List[str]) -> bool:
    """
    True if the paper comes from a blocklisted publisher.

    The blocklist rejects junk publishers arriving via PubMed; it must never be
    applied to the curated journal list or to preprints. A bare substring test
    matched the blocklist entry "Biology" (aimed at MDPI's *Biology*) inside
    Genome Biology, Current Biology, and Molecular Biology and Evolution,
    silently stripping all three tracked journals out of the ranked corpus --
    and it would match three of the seven bioRxiv categories too.
    """
    if paper.is_preprint:
        return False
    journal_lower = paper.journal.strip().lower()
    if journal_lower in {j.strip().lower() for j in tracked_journals}:
        return False
    for blocked in blocklist:
        term = re.escape(blocked.strip().lower())
        if re.search(rf"(?<![a-z0-9]){term}(?![a-z0-9])", journal_lower):
            return True
    return False


def select_top_papers(
    scored_journal: List[Paper],
    journal_tocs: List[JournalTOC],
    scorer: KeywordScorer,
    eligible_dois: set,
    tracked_journals: List[str],
    config: Config,
) -> List[Paper]:
    """
    Build the curated slate: the highest-scoring papers overall, then each
    journal's own best, so a strong issue in a small journal is not crowded out
    by a prolific one.

    Every candidate must be in `eligible_dois` -- the blocklist- and
    notice-filtered corpus. The journal-highlight pass reads `journal_tocs`,
    which is not filtered, and used to be a back door that readmitted papers the
    filters had already rejected.
    """
    tracked_lower = {j.strip().lower() for j in tracked_journals}
    seen: set = set()
    result: List[Paper] = []

    def add(p: Paper) -> None:
        if p.doi in seen:
            return
        if p.doi not in eligible_dois:
            return
        if p.journal.strip().lower() not in tracked_lower:
            return
        seen.add(p.doi)
        result.append(p)

    # Global best first
    for p in scored_journal:
        add(p)

    # Then top-scoring papers per journal, for breadth across the journal list
    for toc in journal_tocs:
        for p in toc.papers:
            if p.keyword_score == 0.0:
                p.keyword_score = scorer.score(p)
        best = sorted(
            [p for p in toc.papers if p.keyword_score >= config.journal_highlight_min_score],
            key=lambda p: p.keyword_score,
            reverse=True,
        )[: config.top_per_journal]
        for p in best:
            add(p)

    result.sort(key=lambda p: p.keyword_score, reverse=True)
    return result[: config.top_n_papers]


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

    # Track every DOI the payload mentions that we cannot place. Enrichments are
    # matched by exact DOI, so a single mangled key used to vanish in silence and
    # leave that paper with a heuristic finding and no summary.
    unmatched: Dict[str, List[str]] = {}

    def _apply(section: str, attr: str) -> None:
        values = enrichments.get(section) or {}
        if not values:
            return
        count = 0
        for doi, text in values.items():
            if doi in doi_index:
                setattr(doi_index[doi], attr, text)
                count += 1
            else:
                unmatched.setdefault(section, []).append(doi)
        console.print(f"  Applied {count}/{len(values)} {section}")

    _apply("findings", "finding")
    _apply("summaries", "summary")

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
        for topic, dois in source_groups.items():
            for d in dois:
                if d not in doi_index:
                    unmatched.setdefault("topic_groups", []).append(d)

    # ── Coverage check ────────────────────────────────────────────────────────
    # A half-applied enrichment must not publish quietly. The 2026-08-01 digest
    # went out with scoring_mode "keyword", no executive summary, and zero
    # summaries, and nothing in the run said so.
    curated = report.top_papers + report.top_preprints
    grouped_dois = {d for dois in (report.stats.topic_groups or {}).values() for d in dois}
    problems: List[str] = []

    for section, dois in unmatched.items():
        problems.append(
            f"{len(dois)} {section} DOI(s) matched no paper: " + ", ".join(dois[:5])
            + (" ..." if len(dois) > 5 else "")
        )
    # Coverage is measured against the payload, not against the Paper objects.
    # run_digest() pre-fills every curated paper with a heuristic finding as an
    # unenriched fallback, so testing p.finding would count that as covered and
    # hide a genuine gap.
    supplied_findings = set(enrichments.get("findings") or {})
    supplied_summaries = set(enrichments.get("summaries") or {})
    for label, missing in (
        ("AI finding", [p for p in curated if p.doi not in supplied_findings]),
        ("summary", [p for p in curated if p.doi not in supplied_summaries]),
        ("topic group", [p for p in curated if p.doi not in grouped_dois]),
    ):
        if missing:
            problems.append(
                f"{len(missing)}/{len(curated)} papers have no {label}: "
                + "; ".join(p.title[:50] for p in missing[:3])
                + (" ..." if len(missing) > 3 else "")
            )
    if not report.stats.executive_summary:
        problems.append("no executive summary")

    # Re-save JSON, HTML, and MD. An incomplete enrichment is still rendered so
    # it can be inspected; the non-zero exit below is what stops it publishing.
    report.to_json(str(json_path))
    output_dir = json_path.parent
    formatter = ReportFormatter()
    html_path, md_path = formatter.save(report, output_dir)
    console.print("\n[bold green]✓ Enriched report saved:[/]")
    console.print(f"  HTML: [link={html_path}]{html_path}[/link]")
    console.print(f"  MD:   {md_path}")

    if problems:
        console.print("\n[bold red]✗ Enrichment is incomplete — do not publish:[/]")
        for p in problems:
            console.print(f"  [red]•[/] {p}")
        console.print(
            "\n[yellow]Fix the enrichment payload and re-run --enrich. "
            "DOIs must be copied verbatim from the .enrich.json file.[/]"
        )
        sys.exit(1)

    console.print(f"\n[green]Coverage complete: {len(curated)} papers, all enriched.[/]")


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

    scorer = KeywordScorer(config)
    formatter = ReportFormatter()

    all_journal_papers: List[Paper] = []
    journal_tocs: List[JournalTOC] = []
    # Journals RSS actually delivered articles for this run. Observed, not assumed —
    # see the stats block below.
    rss_delivered: set[str] = set()
    pubmed = PubMedSource(api_key=config.ncbi_api_key)

    # ── Step 1: Fetch journal RSS feeds ──────────────────────────────────────
    if not skip_journals:
        console.print("\n[bold]Step 1/3: Fetching journal content...[/]")

        # 1a. RSS feeds
        console.print("  [dim]Fetching RSS feeds...[/]")
        rss = RSSFetcher(request_delay=0.8)
        tocs = rss.fetch_all_journals(config.journals, period_start, period_end, verbose=True)

        rss_total = sum(len(t.papers) for t in tocs)
        rss_delivered = {t.journal_name for t in tocs if t.papers}
        console.print(f"  → [green]{rss_total}[/] articles from RSS")

        # 1b. PubMed supplement — fetch all 20 journals via Entrez
        console.print("  [dim]Supplementing with PubMed...[/]")
        pubmed_by_journal: Dict[str, List[Paper]] = {}
        for jc in config.journals:
            try:
                papers = pubmed.search_journal(jc.pubmed_ta, period_start, period_end, max_results=500)
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
        tracked_journal_names = [jc.name for jc in config.journals]
        before_filter = len(all_rss_pubmed)
        all_rss_pubmed = [
            p for p in all_rss_pubmed
            if not is_blocked(p, config.journal_blocklist, tracked_journal_names)
            and not is_editorial_notice(p)
        ]
        dropped = before_filter - len(all_rss_pubmed)
        all_journal_papers = deduplicate(all_rss_pubmed)
        console.print(
            f"  → [green]{len(all_journal_papers)}[/] unique articles after dedup "
            f"[dim]({dropped} dropped: blocklist + correction notices)[/]"
        )

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
    preprint_scan: tuple = (0, None, False)
    if not skip_biorxiv:
        console.print("\n[bold]Step 2/3: Fetching bioRxiv preprints...[/]")
        biorxiv = BioRxivSource(
            request_delay=0.5,
            max_scan=config.biorxiv_max_scan,
            max_seconds=config.biorxiv_max_seconds,
        )
        preprints = biorxiv.search_multi_category(
            since=period_start,
            until=period_end,
            categories=config.biorxiv_categories,
            verbose=True,
        )
        all_preprints = deduplicate([p for p in preprints if not is_editorial_notice(p)])
        preprint_scan = (biorxiv.last_scanned, biorxiv.last_total, biorxiv.last_truncated)
        console.print(f"  → [green]{len(all_preprints)}[/] preprints after dedup")
        if biorxiv.last_truncated:
            console.print(
                f"  [bold yellow]! bioRxiv scan incomplete: {biorxiv.last_scanned} of "
                f"{biorxiv.last_total} records walked. The preprint section is partial.[/]"
            )

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

    # ── Build the curated slate ───────────────────────────────────────────────
    top_papers = select_top_papers(
        scored_journal,
        journal_tocs,
        scorer,
        eligible_dois={p.doi for p in all_journal_papers},
        tracked_journals=[jc.name for jc in config.journals],
        config=config,
    )
    top_preprints = scored_preprints[:config.top_n_preprints]
    console.print(f"  Curated top papers (tracked journals, diverse): {len(top_papers)}")
    console.print(f"  Top preprints: {len(top_preprints)}")

    # ── Heuristic findings (fallback until AI enrichment) ──────────────────
    for p in top_papers + top_preprints:
        p.finding = _heuristic_finding(p)

    # ── Build RSS vs PubMed status ────────────────────────────────────────────
    # Report what actually happened, not what we expected to happen. This used to
    # be a hardcoded list of every configured journal minus a known-broken set,
    # which meant a run where every feed failed still reported RSS as healthy and
    # a total egress failure looked like a quiet fortnight.
    rss_ok_list = [jc.name for jc in config.journals if jc.name in rss_delivered]
    pubmed_only_list = [jc.name for jc in config.journals if jc.name not in rss_delivered]

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
        preprints_scanned=preprint_scan[0],
        preprints_available=preprint_scan[1],
        preprint_scan_truncated=preprint_scan[2],
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

    # ── Print summary ─────────────────────────────────────────────────────────
    console.print("\n[bold green]═══ Digest Summary ═══[/]")
    t = Table(show_header=False, box=None, padding=(0, 1))
    t.add_row("Papers scanned:", f"[cyan]{stats.total_papers_scanned}[/]")
    t.add_row("Preprints:", f"[cyan]{stats.total_preprints}[/]")
    t.add_row("Journals with content:", f"[cyan]{stats.journals_with_content}/{stats.journals_checked}[/]")
    t.add_row("Top journal papers:", f"[green]{len(top_papers)}[/]")
    t.add_row("Top preprints:", f"[green]{len(top_preprints)}[/]")
    if stats.preprints_available:
        # Only show the fraction when it means something. bioRxiv's reported
        # total grows while the scan runs (new preprints post), so a complete
        # scan routinely walks slightly more records than the total it started
        # with -- printing "2832/2828" just looks like a bug.
        if stats.preprint_scan_truncated:
            t.add_row(
                "bioRxiv scan:",
                f"[yellow]{stats.preprints_scanned}/{stats.preprints_available}"
                f" — INCOMPLETE[/]",
            )
        else:
            t.add_row("bioRxiv scan:", f"[cyan]{stats.preprints_scanned} (complete)[/]")
    console.print(t)

    if dry_run:
        console.print("\n[yellow]Dry run — no report saved.[/]")
        return

    # Full JSON — the re-render source for --enrich
    json_path = output_dir / f"digest_{today.strftime('%Y-%m-%d')}.json"
    report.to_json(str(json_path))

    # Slim JSON — what the AI enrichment step reads. Kept separate because the
    # full digest is megabytes of abstracts the enrichment never needs, which is
    # far too much to pull into a context window.
    enrich_path = output_dir / f"digest_{today.strftime('%Y-%m-%d')}.enrich.json"
    with open(enrich_path, "w", encoding="utf-8") as f:
        json.dump(report.to_enrich_dict(), f, indent=1, ensure_ascii=False)

    html_path, md_path = formatter.save(report, output_dir)
    console.print("\n[bold green]✓ Report saved:[/]")
    console.print(f"  JSON:   {json_path} [dim]({json_path.stat().st_size/1e6:.1f} MB, re-render source)[/]")
    console.print(f"  ENRICH: {enrich_path} [dim]({enrich_path.stat().st_size/1e3:.0f} KB — read this one)[/]")
    console.print(f"  HTML:   [link={html_path}]{html_path}[/link]")
    console.print(f"  MD:     {md_path}")
    console.print("\n[dim]Enrich with AI: use /digest skill in Claude Code[/]")


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
