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
2. Reads the generated JSON
3. Generates executive summary, can't-miss picks, and lay summaries
4. Applies enrichments via `--enrich` mode
5. Copies to `reports/latest_digest.{html,md}` and publishes to GitHub Pages via `publish_digest.sh`

Usage: `/digest 15` (or `/digest` for the default 15-day lookback)

This command is the entire unattended routine — the scheduled cloud agent runs nothing else.

## Environment Variables

- `NCBI_API_KEY` — optional, speeds up PubMed queries (higher rate limit)

## Architecture

The pipeline runs in 4 sequential steps orchestrated by `run_review.py:run_digest()`:

1. **Fetch journals** — RSS feeds (`RSSFetcher`) supplemented by PubMed Entrez (`PubMedSource.search_journal`), merged and deduplicated into `JournalTOC` objects. Missing abstracts are enriched via `PubMedSource.enrich_abstracts()`.
2. **Fetch bioRxiv** — `BioRxivSource` queries the bioRxiv REST API across configured categories
3. **Score & rank** — `KeywordScorer` does weighted tier-based keyword matching (title hits get a multiplier)
4. **Generate report** — `ReportFormatter` renders Jinja2 HTML template + Markdown, plus JSON for AI enrichment. Saved to `reports/`

The `--enrich` mode loads a digest JSON, applies enrichments (executive summary, lay summaries, can't-miss picks) from stdin, and re-renders HTML/MD.

### Key modules under `lit_review/`

- `models.py` — dataclasses with JSON serialization: `Paper`, `JournalTOC`, `DigestStats`, `DigestReport`
- `config.py` — loads `config.yaml` into typed `Config` dataclass
- `cache.py` — SQLite cache (`cache/lit_cache.db`) for papers, feed ETags, and report inclusion tracking
- `sources/rss_fetcher.py` — RSS/Atom feed fetching with ETag caching and date filtering
- `sources/pubmed.py` — PubMed Entrez esearch/efetch; also handles abstract enrichment and "classics" (3-12 month lookback)
- `sources/biorxiv.py` — bioRxiv REST API client with category filtering
- `ranking/keyword_scorer.py` — tiered keyword matching with configurable weights and title multipliers
- `ranking/llm_scorer.py` — legacy Claude API-based scoring (not used in current pipeline)
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
cache/                  gitignored — SQLite RSS ETags; safe to regenerate
```

- **Publish**: `bash publish_digest.sh` copies `reports/latest_digest.html` into
  `site/index.html` + `site/digest_YYYY-MM-DD.html`, commits, and pushes `main`. The
  `Deploy to GitHub Pages` workflow then serves `site/` at
  https://echuong.github.io/chuong-lab-digest/
- **Schedule**: a Claude Code cloud routine fires `/digest 15` on the 1st & 15th
  (`0 7 1,15 * *`). It lives in the Claude account, not this repo — recreate it with the
  `/schedule` skill after any account migration.
- **No Obsidian vault step.** Dropped 2026-08-19: a cloud session cannot reach the local
  vault at `~/Documents/Obsidian Vault`. The public site is the durable archive.
  `to_vault.py` is retained as a local-only utility and is not part of the flow.
- **The `cache/` directory is disposable.** `CacheManager.was_included_in_report()` is never
  called anywhere in the pipeline, so the cache only saves RSS bandwidth via ETags. A cloud
  run starting with an empty cache produces the same digest.

## Configuration

All tuning happens in `config.yaml`:
- **Journals**: name, RSS URL, and PubMed title abbreviation (`pubmed_ta`) for each tracked journal
- **Keyword tiers**: hierarchical scoring with tier weights and title multipliers (tier1_core=10, tier2_biology=7, tier3_methods=5, etc.)
- **Threshold**: `keyword_score_threshold` (default 3.0)
- **Blocklist**: publisher/journal names to reject (Frontiers, MDPI, etc.)
- **bioRxiv categories**: which subject areas to fetch

## Known Issues

- MBE (Molecular Biology and Evolution) RSS is broken (OUP Cloudflare 403) — relies on PubMed-only fallback
- bioRxiv API returns max 100 results per page; large date ranges require pagination
- No tests exist yet
- The cloud routine has no failure alerting: if a scheduled run errors, the site simply keeps showing the previous digest
