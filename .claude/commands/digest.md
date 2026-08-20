---
description: Run the bi-weekly literature digest with AI-enriched summaries and publish to GitHub Pages
argument-hint: "[days] (default 15)"
allowed-tools: Bash, Read, Write
---

Run the Chuong Lab literature digest with AI enrichment, then publish it.

## Step 1 — Run the pipeline

```
pip install -q -r requirements.txt
python run_review.py --days 15
```

Use `$ARGUMENTS` as the `--days` value if one was given; otherwise 15, matching the 1st & 15th
schedule. Note the paths it prints. Budget ~10-15 minutes: the bioRxiv scan walks ~100 sequential
pages and prints progress as it goes.

## Step 2 — Read the slim JSON

Read the file printed as `ENRICH`: `reports/digest_<date>.enrich.json` (~60 KB).

**Never read `digest_<date>.json`.** That is the full re-render source — megabytes of abstracts for
every paper scanned. It will not fit in context and you do not need it.

The slim file contains:
- `top_papers` / `top_preprints` — the curated slate, with full abstracts, DOIs, and scores
- `journal_tocs` — `journal_name` + `paper_count` per journal that had papers
- `stats` — counts for the period, plus the two coverage signals below

Check both before writing anything, and report a shortfall rather than presenting a thin digest as
a quiet fortnight:
- `rss_ok` empty while `pubmed_only` lists every journal → RSS failed wholesale this run.
- `preprint_scan_truncated` true → the bioRxiv scan stopped early
  (`preprints_scanned` of `preprints_available`), so the preprint section is partial.

## Step 3 — Write the enrichments

You are a research assistant for the Chuong Lab at CU Boulder. The lab studies transposable
elements as drivers of gene regulatory evolution, with emphasis on immune gene networks and cancer.
Judge relevance against these active themes, and explicitly call out any paper bearing on one:

- TE-derived alternative isoforms of immune receptor and signaling genes, and their consequences
  for pathway output (published exemplar: the IFNAR2 Alu-derived decoy receptor)
- Non-canonical transcript architecture driven by intronic TEs — intronic polyadenylation,
  alternative TSS usage, premature termination in innate immune signaling genes
- ERV/LTR-derived enhancers reactivated in cancer, in gastrointestinal and gynecologic tumors, and
  their upstream signaling dependencies (published exemplar: LTR10 in CRC)
- Chromatin silencing pathways (H3K9 methyltransferases and their complexes) that restrain TE
  derepression and the viral-mimicry response in tumors
- Epitranscriptomic control of TE-derived transcripts — m6A writers/readers/erasers, and the effect
  of inhibiting them on TE exonization
- Structural and repeat variation as regulatory variation: polymorphic TE insertions, VNTRs,
  pangenome graph references, especially at polymorphic immune loci
- Repeat-expansion and structural-variant mechanisms in neurological disease, where a TE or repeat
  alters regulation of a single causal gene
- Long-read isoform discovery in primary human immune cells
- Sequence-to-function deep learning models for predicting regulatory variant effects
- Comparative immunogenomics across primates, rodents, bats, ruminants

Themes are stated at the level of mechanism, not named unpublished target genes, because this repo
is public.

### Ground rules

- **Do not fabricate.** State only numbers, effect sizes, and findings present in the abstract you
  were given. Where an abstract has no quantitative result, describe the result qualitatively —
  never invent a figure.
- **Copy DOIs verbatim** from the slim JSON. Enrichments are matched by exact DOI; a reconstructed
  or guessed DOI is dropped and fails the run.
- **Cover every paper.** Each DOI in `top_papers` + `top_preprints` needs exactly one topic group,
  one finding, and one summary.

### `executive_summary`

3–4 paragraphs. Lead with the 2–3 can't-miss papers and why they matter, then synthesize across the
digest — draw explicit connections between findings and to the lab's systems (IFNAR2 decoy receptor,
MER41/LTR10 enhancers, TE exonization, m6A, pan-genome TE variation). Name papers, genes, TE
families, and pathways. Direct, mechanistic style. Separate paragraphs with `\n\n`. Link papers as
`[Author et al.](https://doi.org/DOI)` — these render as clickable links.

### `topic_groups`

Sort every paper into exactly one of these, dropping any category with no papers. Order papers
within a group most-relevant first.

TE Biology · Gene Regulation · Immunity & Inflammation · Cancer · RNA Biology ·
Genomics & Methods · Evolution & Comparative

### `findings`

One sentence per paper, max 20 words: the **key finding or mechanism**, not a relevance statement.
Name the gene, pathway, TE family, organism.

> "LTR10 ERVs are reactivated as cancer-specific enhancers in CRC via MAPK/AP1 signaling."

### `summaries`

3–5 sentences per paper, educated-layman level. The finding already states *what they found*, so do
not restate it — add what surrounds it: the question being addressed, the mechanism and any
concrete numbers from the abstract, why it matters to the field, and the explicit tie to a Chuong
Lab system (name the TE family, gene, or pathway). Omit the tie only where there genuinely is none.
No "this is relevant to the lab" filler.

## Step 4 — Apply the enrichments

Write the payload to a temp file and pipe it in:

```
python run_review.py --enrich reports/digest_<date>.json < /tmp/enrichments.json
```

Payload shape:

```json
{
  "executive_summary": "Paragraph with [Author et al.](https://doi.org/10.1234/abc) links.\n\nSecond paragraph.",
  "topic_groups": {"TE Biology": ["10.1234/abc"], "Cancer": ["10.5678/def"]},
  "findings": {"10.1234/abc": "LTR10 ERVs are reactivated as enhancers in CRC via MAPK/AP1."},
  "summaries": {"10.1234/abc": "This study asks how ERV-derived sequences acquire enhancer activity in colorectal tumors. CRISPR deletion of individual LTR10 copies reduced nearby oncogene expression, and MEK inhibition suppressed the elements' activity, making them pharmacologically addressable. It parallels the lab's published LTR10/CRC system directly."}
}
```

`--enrich` takes the **full** `digest_<date>.json`, not the `.enrich.json` you read in Step 2 — the
full file is the re-render source, so the enriched HTML keeps every journal table of contents.

**The command exits non-zero if the enrichment is incomplete** — unmatched DOIs, a paper with no
finding/summary/topic group, or a missing executive summary. If it fails, read what it names, fix
the payload, and re-run. Do not proceed to Step 5 on a non-zero exit.

## Step 5 — Publish

```
bash publish_digest.sh <date>
```

Pass the digest date. The script reads `reports/digest_<date>.html`, copies it to
`site/index.html` and `site/digest_<date>.html`, commits, and pushes via `HEAD:main` — correct
whether the checkout sits on a branch or at the detached HEAD a cloud session gets. Pushing
`origin main` from a detached HEAD sends a stale local ref and published nothing on 2026-08-20.
If the remote moved while the digest was building, the script rebases onto it and retries once.

Confirm the commit happened. "No changes to publish" means the digest did not change anything and
something upstream failed. If the push genuinely cannot reach `main`, or the rebase hits a real
conflict in `site/`, push a branch and open a PR instead — and say so in your report rather than
failing silently.

## Step 6 — Report

Give the digest date, papers scanned, counts of top papers and preprints, the 2–3 can't-miss picks
by title, and the published URL: https://echuong.github.io/chuong-lab-digest/

Locally, `open reports/digest_<date>.html` also works.
