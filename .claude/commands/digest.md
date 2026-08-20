---
description: Run the bi-weekly literature digest with AI-enhanced summaries and publish to GitHub Pages
argument: Number of days to search (default 15)
---

Run the Chuong Lab literature digest pipeline with AI enrichment.

## Step 1: Run the keyword-scoring pipeline

Install dependencies first — a cloud session starts from a bare checkout, and this is a
no-op on a machine that already has them:

```
pip install -q -r requirements.txt
```

Then run the digest script. Use the argument as --days if provided; the default lookback is
15 days, matching the 1st & 15th schedule:

```
python run_review.py --days $ARGUMENTS
```

If $ARGUMENTS is empty, run `python run_review.py --days 15`.

Wait for it to complete and note the JSON file path from the output.

## Step 2: Read the digest JSON

Read **`reports/digest_YYYY-MM-DD.enrich.json`** — the slim enrichment view, around 80 KB.

**Do not read `digest_YYYY-MM-DD.json`.** That is the full re-render source: megabytes of
abstracts for every paper scanned, most of which this step never mentions. Reading it wastes
the context window on data you will not use, and on a busy fortnight it will not fit at all.
The pipeline prints both paths and their sizes; take the one labelled `ENRICH`.

The slim file contains exactly what the enrichment needs:
- `top_papers`: the top keyword-scored journal papers, with full abstracts, DOIs, and scores
- `top_preprints`: the top bioRxiv preprints, same shape
- `journal_tocs`: one entry per journal that had papers — `journal_name`, `paper_count`, and
  `top_titles` (the highest-scoring titles with scores, no abstracts). Enough to orient the
  per-journal mini-summaries; write those from the titles.
- `stats`: counts for the period, including `rss_ok` and `pubmed_only`

If `stats.rss_ok` is empty while `pubmed_only` lists every journal, RSS wholly failed this run —
say so in your report rather than presenting a thin digest as a quiet fortnight.

## Step 3: Generate AI enrichments

Based on the paper data, generate the following enrichments. You are a research assistant for the Chuong Lab at CU Boulder, which studies:
- Transposable elements (ERVs, SINEs, LINEs, Alu) as drivers of gene regulatory evolution
- TE co-option as enhancers in innate immune networks (type I IFN, inflammasome)
- TE exonization generating novel protein isoforms (IFNAR2/Alu decoy receptor)
- TE reactivation in cancer creating neo-enhancers (LTR10 in CRC, ERVs in ovarian cancer)
- m6A RNA methylation, pan-genome TE variation, long-read transcriptomics
- Comparative immunogenomics across primates, rodents, bats, ruminants

**Tailor to the lab's currently active project themes**, and explicitly call out any paper
that bears on one of them or on an active grant aim:

- TE-derived alternative isoforms of immune receptor and signaling genes, and their
  consequences for pathway output (published exemplar: the IFNAR2 Alu-derived decoy receptor)
- Non-canonical transcript architecture driven by intronic TEs — intronic polyadenylation,
  alternative TSS usage, and premature termination in innate immune signaling genes
- ERV/LTR-derived enhancers reactivated in cancer, in both gastrointestinal and gynecologic
  tumors, and their upstream signaling dependencies (published exemplar: LTR10 in CRC)
- Chromatin silencing pathways (H3K9 methyltransferases and their complexes) that restrain
  TE derepression and the viral-mimicry response in tumors
- Epitranscriptomic control of TE-derived transcripts — m6A writers/readers/erasers and the
  effect of their inhibition on TE exonization
- Structural and repeat variation as regulatory variation: polymorphic TE insertions, VNTRs,
  and pangenome graph references, especially at highly polymorphic immune loci
- Repeat-expansion and structural-variant mechanisms in neurological and neurodevelopmental
  disease, where a TE or repeat alters regulation of a single causal gene
- Long-read isoform discovery in primary human immune cells
- Sequence-to-function deep learning models for predicting regulatory variant effects

These themes are deliberately stated at the level of mechanism rather than naming specific
unpublished target genes, because this repo is public. The gene-level project list lives in
`04-Projects/` in the private Obsidian vault — consult it there when running locally, and
refresh these themes here if the program shifts.

### Executive Summary
Write a thorough 3-4 paragraph executive summary of the most important themes in this digest. Lead with the 2-3 can't-miss papers and say why they matter. Then synthesize across papers — draw explicit connections between findings and to the lab's systems (IFNAR2 decoy receptor, MER41/LTR10 enhancers, TE exonization, m6A, pan-genome TE variation). Be specific: name papers, genes, TE families, pathways, and effect sizes. Write in a direct, mechanistic style. Separate paragraphs with \n\n.

When referencing specific papers, use markdown-style hyperlinks: `[Author et al.](https://doi.org/DOI)`. These become clickable in the HTML report.

### Topic Groups
Categorize ALL papers from top_papers and top_preprints into topic groups. Use these categories (only include categories that have papers):
- **TE Biology** — papers directly about transposable elements, retrotransposons, ERVs, SINEs, LINEs
- **Gene Regulation** — enhancers, promoters, chromatin, transcription factors, epigenetics (not TE-specific)
- **Immunity & Inflammation** — innate/adaptive immunity, interferon signaling, cytokines, inflammasome
- **Cancer** — oncology, tumor biology, immunotherapy, cancer genomics
- **RNA Biology** — splicing, isoforms, m6A, RNA modifications, long-read transcriptomics
- **Genomics & Methods** — pan-genomes, CRISPR screens, sequencing methods, computational tools
- **Evolution & Comparative** — evolutionary genomics, cross-species comparisons, phylogenetics

Each paper should appear in exactly one group (pick the most relevant). **Order papers within each group by relevance** (most relevant to the lab first). Map each category to a list of DOIs.

### Findings
For each paper in top_papers and top_preprints, write ONE sentence (max 20 words) stating the **key finding or mechanism**. This is NOT a relevance statement — it is what the paper actually discovered.

Rules:
- Be specific: name the gene, pathway, TE family, organism
- Example: "LTR10 ERVs are reactivated as cancer-specific enhancers in CRC via MAPK/AP1 signaling."
- Example: "AIM2 undergoes liquid-liquid phase separation upon dsDNA binding via OB1/OB2 domains."
- Do NOT write "relevant to the lab" — that's for summaries

### Summaries
For each paper in top_papers and top_preprints, write 3-5 sentences at an educated layman level:
1. What is this paper about, and what question does it address? (one sentence)
2. What did they find — the key result, mechanism, and any concrete numbers/effect sizes? (1-2 sentences)
3. Why does it matter to the field? (one sentence)
4. If applicable, the explicit tie to Chuong Lab systems — name the TE family, gene, or pathway (e.g., MER41/AIM2, LTR10/CRC, IFNAR2 exonization). Omit this sentence only when there is genuinely no connection. (one sentence)

Be concrete and specific; avoid generic "this is relevant to the lab" filler.

### Journal Summaries
Using the `journal_tocs` array in the JSON (each has `journal_name` and its `papers`), write a 1-2 sentence mini-summary for each journal that has papers this period. Orient the reader: highlight the most notable or most lab-relevant article(s) in that journal's table of contents this period, naming the topic or finding. If a journal has nothing especially relevant, a single neutral sentence noting the general theme is fine. Skip journals with no papers.

Map each journal's exact `journal_name` (as it appears in the JSON) to its summary string.

## Step 4: Apply enrichments

Create the enrichments JSON and pipe it to the enrich command. The JSON format is:

```json
{
  "executive_summary": "First paragraph text with [Author et al.](https://doi.org/DOI) links.\n\nSecond paragraph with more findings.",
  "topic_groups": {
    "TE Biology": ["10.1234/...", "10.5678/..."],
    "Cancer": ["10.9999/..."],
    "Immunity & Inflammation": ["10.1111/..."]
  },
  "findings": {
    "10.1234/...": "LTR10 ERVs are reactivated as enhancers in CRC via MAPK/AP1.",
    "10.5678/...": "AIM2 undergoes phase separation on dsDNA binding."
  },
  "summaries": {
    "10.1234/...": "This study examines LTR10 endogenous retroviruses in colorectal cancer. The authors found these elements are reactivated as cancer-specific enhancers driven by MAPK/AP1 signaling, and CRISPR deletion of individual LTR10 copies reduced expression of nearby oncogenes. Inhibition with MEK inhibitors suppressed their activity, pointing to TE enhancers as druggable targets. This directly parallels the lab's work on TE-driven gene regulation in cancer, including the published LTR10/CRC system.",
    "10.5678/...": "This paper investigates how AIM2 senses cytosolic DNA to activate inflammasomes. They demonstrate AIM2 undergoes liquid-liquid phase separation upon dsDNA binding through its HIN domain, concentrating downstream signaling components. The finding refines the mechanistic model of DNA-sensing innate immunity. It connects to the lab's work because MER41 ERV elements were co-opted as IFN-inducible enhancers regulating AIM2 expression."
  },
  "journal_summaries": {
    "Cell": "Two papers on chromatin regulation stand out, including one mapping enhancer-promoter contacts genome-wide.",
    "Nature Genetics": "A pan-genome graph reference for human structural variation — directly relevant to the lab's polymorphic TE insertion work."
  }
}
```

Write this JSON to a temp file, then run:

```
python run_review.py --enrich reports/digest_YYYY-MM-DD.json < /tmp/enrichments.json
```

Note the filename: `--enrich` takes the **full** `digest_YYYY-MM-DD.json`, not the `.enrich.json`
you read in Step 2. The full file is the re-render source, so the enriched HTML keeps every
journal table of contents; the slim file exists only to be read. Enrichments are matched to
papers by DOI, so the DOIs in your JSON must be copied exactly from the slim file.

## Step 5: Publish to GitHub Pages

GitHub Pages is the **main form** of the digest. Code and site live in one repo
(`echuong/chuong-lab-digest`): the pipeline at the root, the published site in `site/`.
Publishing is an in-repo commit, so it needs no extra credentials and works the same
locally and in a cloud session.

```
bash publish_digest.sh YYYY-MM-DD
```

That is the whole publish step — pass the digest date and the script does the rest. It reads
`reports/digest_YYYY-MM-DD.html` directly, copies it to `site/index.html` and
`site/digest_YYYY-MM-DD.html`, commits, and pushes to `main` (via `HEAD:main`, which is
correct whether the checkout is on a branch or at the detached HEAD a cloud session gets).
The `Deploy to GitHub Pages` workflow then publishes `site/`. If the remote moved while the
digest was being built, the script rebases onto it and retries once on its own.

There is no `reports/latest_digest.*` step any more — that indirection was removed on
2026-08-20; nothing else ever read those files.

If the push still fails — the environment genuinely cannot write to `main`, or the rebase hits
a real conflict in `site/` — push a branch and open a PR instead, and say so explicitly in the
final report rather than failing silently.

## Step 6: Report

State the published URL and the local paths:

```
https://echuong.github.io/chuong-lab-digest/
```

Locally you can also open the file directly:

```
open reports/digest_YYYY-MM-DD.html
```

When running as the scheduled cloud routine, report: the digest date, papers scanned, number
of top papers and preprints, the 2-3 can't-miss picks by title, and the published URL.

## Setup & portability

Check after any account or machine migration:

1. **Scheduled routine** — a Claude Code cloud routine runs this command on the 1st & 15th
   (`0 7 1,15 * *`) against `echuong/chuong-lab-digest`. It lives in the Claude account, not
   this repo; recreate it with the `/schedule` skill and keep the prompt thin (`/digest 15`).
2. **Single repo** — `echuong/chuong-lab-digest` holds both the pipeline and the site.
   Pages deploys from `site/` via `.github/workflows/deploy.yml`. `reports/` and `cache/`
   are gitignored as ephemeral working output.
3. **No vault archiving** — dropped 2026-08-19 because a cloud session cannot reach the local
   Obsidian vault. The public site is the durable archive. `to_vault.py` remains in the repo
   as a local-only utility and is not part of this flow.
4. **Interest tuning** — `config.yaml` (7 keyword tiers, journal list, blocklist) is the
   machine source, mirrored for humans at `08-Literature/Literature interests.md` in the
   vault. Retune both together.
5. `NCBI_API_KEY` (optional) raises the PubMed rate limit. The pipeline runs fine without it.
