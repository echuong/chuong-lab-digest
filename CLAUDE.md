# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A bi-weekly literature digest tool for the Chuong Lab (CU Boulder). It fetches recent papers from journal RSS feeds, PubMed, and bioRxiv, scores them for relevance using keyword matching, then outputs HTML+Markdown+JSON digest reports. AI-enhanced summaries are generated via the `/digest` Claude Code skill, and the result is published to GitHub Pages.

This repo is `echuong/chuong-lab-digest` — it holds **both** the pipeline (root) and the published site (`site/`). It runs unattended as a Claude Code cloud routine on the 1st & 15th; see "Deployment" below.

## Running the Digest

```bash
# Install dependencies
pip install -r requirements.txt

# Basic run (keyword scoring, 30-day lookback) — outputs JSON + HTML + MD
python run_review.py

# Custom date range
python run_review.py --days 14

# Source filtering
python run_review.py --journals-only    # skip bioRxiv
python run_review.py --preprints-only   # skip journal RSS

# Preview without saving
python run_review.py --dry-run

# Apply AI enrichments from Claude Code (reads enrichments JSON from stdin):
python run_review.py --enrich reports/digest_2026-03-22.json
```

### /digest skill

The `/digest` skill automates the full AI-enriched pipeline:
1. Runs `python run_review.py --days N` (keyword scoring)
2. Reads the slim `.enrich.json`
3. Generates the executive summary, per-paper findings, lay summaries, and topic groups
4. Applies enrichments via `--enrich` mode, which **fails non-zero on incomplete coverage**
5. Publishes to GitHub Pages via `bash publish_digest.sh YYYY-MM-DD`

Usage: `/digest 15` (or `/digest` for the default 15-day lookback)

This command is the entire unattended routine — the scheduled cloud agent runs nothing else.

## Environment Variables

- `NCBI_API_KEY` — optional, speeds up PubMed queries (higher rate limit)

## Architecture

The pipeline runs in 4 sequential steps orchestrated by `run_review.py:run_digest()`:

1. **Fetch journals** — RSS feeds (`RSSFetcher`) supplemented by PubMed Entrez (`PubMedSource.search_journal`), merged and deduplicated into `JournalTOC` objects. Missing abstracts are enriched via `PubMedSource.enrich_abstracts()`.
2. **Fetch bioRxiv** — `BioRxivSource` queries the bioRxiv REST API across configured categories
3. **Score & rank** — `KeywordScorer` does weighted tier-based keyword matching (title hits get a multiplier)
4. **Generate report** — `ReportFormatter` renders Jinja2 HTML template + Markdown, plus **two**
   JSON files in `reports/`:
   - `digest_YYYY-MM-DD.json` — full re-render source, ~1.5 MB. Feed this to `--enrich`.
   - `digest_YYYY-MM-DD.enrich.json` — slim view for the AI, ~60 KB. **Read this one.** It keeps
     abstracts only for the papers being written about and reduces each journal TOC to a name
     and a count. Reading the full JSON instead costs ~750K tokens for data the enrichment
     never uses.

   `all_journal_papers` / `all_preprints` are intentionally *not* serialized: they exist only so
   the scorer can rank the whole corpus in memory, and the template never renders them. Their
   counts live in `stats`.

The `--enrich` mode loads a digest JSON, applies enrichments (executive summary, findings,
summaries, topic groups) from stdin, and re-renders HTML/MD. It reports any DOI that matched no
paper and **exits non-zero if coverage is incomplete** — a half-applied enrichment must not publish
quietly, which is how the 2026-08-01 digest went out unenriched and unnoticed.

### Key modules under `lit_review/`

- `models.py` — dataclasses with JSON serialization: `Paper`, `JournalTOC`, `DigestStats`, `DigestReport`
- `config.py` — loads `config.yaml` into typed `Config` dataclass
- `sources/rss_fetcher.py` — RSS/Atom feed fetching with ETag caching and date filtering
- `sources/pubmed.py` — PubMed Entrez esearch/efetch; also handles abstract enrichment
- `sources/biorxiv.py` — bioRxiv REST API client with category filtering
- `ranking/keyword_scorer.py` — tiered keyword matching with configurable weights and title multipliers
- `report/formatter.py` — Jinja2 HTML + Markdown generation
- `report/template.html` — the HTML report template

## Deployment

The pipeline and the public site live in one repo, so publishing needs no cross-repo
credentials and behaves identically on a laptop and in a cloud session.

```
/                       pipeline code (run_review.py, lit_review/, config.yaml)
site/                   published GitHub Pages content — index.html + digest_YYYY-MM-DD.html
.github/workflows/      deploy.yml: uploads site/ as the Pages artifact on push to main
reports/                gitignored — ephemeral working output
```

- **Publish**: `bash publish_digest.sh YYYY-MM-DD` copies `reports/digest_YYYY-MM-DD.html`
  into `site/index.html` + `site/digest_YYYY-MM-DD.html`, commits, and pushes `main`. The
  `Deploy to GitHub Pages` workflow then serves `site/` at
  https://echuong.github.io/chuong-lab-digest/
  It pushes `HEAD:main`, not `main`: a cloud session checks the repo out at a **detached
  HEAD** with a stale local `main` ref, so `git push origin main` pushes an old commit and is
  rejected (this silently broke the 2026-08-20 run until fixed). If the remote moved during
  the run, the script fetches, rebases onto it, and retries once.
- **Schedule**: cloud routine **Literature digest** (`trig_01TRvC25efZVbf6CNr8ac5dm`), cron
  `13 13 1,15 * *` UTC = 7:13am MDT on the 1st & 15th, model `claude-opus-5`, no MCP connectors.
  Manage at https://claude.ai/code/routines — it lives in the Claude account, not this repo, so
  recreate it with the `/schedule` skill after any account migration. Created 2026-08-20; the
  routine this file previously documented did not exist, which is why the 2026-08-15 cycle
  produced nothing at all.
- **No Obsidian vault step.** Dropped 2026-08-19: a cloud session cannot reach the local
  vault at `~/Documents/Obsidian Vault`. The public site is the durable archive.
  `to_vault.py` is retained as a local-only utility and is not part of the flow.
- **There is no cache.** `lit_review/cache.py` was deleted 2026-08-19. Its only live function was
  conditional RSS requests, and those were actively harmful: only ETags were stored, never feed
  bodies, so an HTTP 304 made `fetch_journal` return an *empty* TOC and the journal was then
  reported as `pubmed_only`. Cloud runs started empty and never hit it; repeat local runs hit it
  every time. Everything else in the module — a papers table nothing queried, filled by ~2,000
  separate SQLite connections per run — had no readers at all.

## Configuration

All tuning happens in `config.yaml`:
- **Journals**: name, RSS URL, and PubMed title abbreviation (`pubmed_ta`) for each tracked journal
- **Keyword tiers**: hierarchical scoring with tier weights and title multipliers (tier1_core=10, tier2_biology=7, tier3_methods=5, etc.)
- **Threshold**: `keyword_score_threshold` (default 3.0)
- **Slate size**: `top_n_papers` / `top_n_preprints` are the real final counts, plus
  `top_per_journal` and `journal_highlight_min_score` for the journal-breadth pass
- **Blocklist**: publisher/journal names to reject (Frontiers, MDPI, etc.). Matched **whole-word**,
  and never applied to tracked journals or preprints — as a bare substring, the entry `"Biology"`
  matched Genome Biology, Current Biology, and Molecular Biology and Evolution, and silently
  removed all three tracked journals from the ranked corpus
- **bioRxiv categories**: which subject areas to fetch

## Scoring notes

`KeywordScorer` credits each term **at most once per field**, and ignores a term nested inside a
longer match (`retrotransposon` inside `LTR retrotransposon`). The score measures how many distinct
relevant concepts a paper touches. It previously added `weight x occurrence count`, which let one
repeated word dominate: in the 2026-08-19 run the top-ranked paper was a telomere/aging study
drawing 64 of its 72 points from five mentions of "STING", ranked above every TE paper in the
corpus. Correction and erratum records are filtered out (`is_editorial_notice`) — they inherit the
title and abstract of the article they amend and so scored just as highly.

## Known Issues

- MBE (Molecular Biology and Evolution) RSS is broken (OUP Cloudflare 403) — relies on PubMed-only fallback
- bioRxiv serves **30 results per page** (not 100). `biorxiv.py` pages off the API's own
  reported `total` rather than an assumed page width, capped at `MAX_SCAN` records, and retries
  a dropped page. The API is flaky enough that retries sometimes still run out: when that happens
  the scan ends early, which is **not** fatal — it is recorded in
  `stats.preprint_scan_truncated` / `preprints_scanned` / `preprints_available`, shown in the run
  summary, and checked by the `/digest` skill, so a partial preprint corpus cannot pass as a quiet
  fortnight. A full 14-day window is ~2,900 records and takes ~10 minutes
- No tests exist yet
- The cloud routine has no failure alerting: if a scheduled run errors, the site simply keeps showing the previous digest
