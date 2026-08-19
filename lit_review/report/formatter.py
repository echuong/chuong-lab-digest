"""Report formatter: generates HTML (and Markdown fallback) from DigestReport."""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from markupsafe import Markup
from jinja2 import Environment, FileSystemLoader

from ..models import DigestReport


TEMPLATE_DIR = Path(__file__).parent

_MD_LINK_RE = re.compile(r'\[([^\]]+)\]\((https?://[^\)]+)\)')

# Journals ranked by general impact (Cell/Nature/Science first, then high-impact,
# then the rest). Journals not listed sort after all ranked ones, alphabetically.
_IMPACT_ORDER = [
    "Cell", "Nature", "Science",
    "Nature Medicine", "Nature Genetics", "Nature Biotechnology",
    "Immunity", "Nature Immunology", "Nature Methods", "Molecular Cell",
    "Nature Reviews Genetics", "Nature Communications", "Science Advances",
    "Genome Research", "Genome Biology", "PNAS", "Current Biology",
    "Molecular Biology and Evolution", "eLife", "Cell Reports",
]
_IMPACT_RANK = {name: i for i, name in enumerate(_IMPACT_ORDER)}


def _impact_key(journal_name: str) -> tuple:
    """Sort key: ranked journals by rank, unknown journals last (alphabetical)."""
    rank = _IMPACT_RANK.get(journal_name)
    if rank is None:
        return (1, journal_name.lower())
    return (0, rank)


def impact_sort(tocs):
    """Sort a list of JournalTOC objects by general impact factor."""
    return sorted(tocs, key=lambda t: _impact_key(t.journal_name))


def _score_class(score: float) -> str:
    if score >= 7:
        return "score-high"
    elif score >= 4:
        return "score-mid"
    return "score-low"


def _linkify_refs(text: str) -> Markup:
    """Convert markdown-style [text](url) links to HTML <a> tags."""
    def _replace(m):
        label = m.group(1)
        url = m.group(2)
        return f'<a href="{url}" target="_blank" rel="noopener">{label}</a>'
    return Markup(_MD_LINK_RE.sub(_replace, text))


def _paragraphs(text: str) -> Markup:
    """Convert text with \\n\\n paragraph breaks and [text](url) links to HTML paragraphs."""
    paras = text.split('\n\n')
    html_parts = []
    for p in paras:
        p = p.strip()
        if not p:
            continue
        linked = _MD_LINK_RE.sub(
            lambda m: f'<a href="{m.group(2)}" target="_blank" rel="noopener">{m.group(1)}</a>',
            p,
        )
        html_parts.append(f'<p>{linked}</p>')
    return Markup('\n'.join(html_parts))


def _slug(text: str) -> str:
    """Convert text to URL-safe slug."""
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')


class ReportFormatter:
    """Assembles the final HTML digest from a DigestReport."""

    def __init__(self):
        self._env = Environment(
            loader=FileSystemLoader(str(TEMPLATE_DIR)),
            autoescape=True,
        )
        self._env.filters["score_class"] = _score_class
        self._env.filters["linkify_refs"] = _linkify_refs
        self._env.filters["slug"] = _slug
        self._env.filters["paragraphs"] = _paragraphs
        self._env.filters["impact_sort"] = impact_sort

    def generate_html(self, report: DigestReport) -> str:
        """Render the Jinja2 HTML template and return the HTML string."""
        template = self._env.get_template("template.html")
        return template.render(report=report)

    def generate_markdown(self, report: DigestReport) -> str:
        """Generate a simple Markdown version as a fallback."""
        lines = [
            f"# Literature Digest — {report.generated_date.strftime('%B %d, %Y')}",
            f"\n**Period**: {report.period_start} to {report.period_end}  ",
            f"**Scanned**: {report.stats.total_papers_scanned} journal papers, {report.stats.total_preprints} preprints  ",
            f"**Scoring**: {report.stats.scoring_mode}\n",
        ]

        # Curated highlights by topic
        if report.stats.topic_groups_resolved:
            lines.append("---\n")
            lines.append("## Curated Highlights\n")
            for topic, papers in report.stats.topic_groups_resolved.items():
                lines.append(f"### {topic}\n")
                for p in papers:
                    lines.append(f"- **{p.title}** | {p.pub_date.year} | {p.abbrev_authors} | {p.journal}")
                    if p.finding:
                        lines.append(f"  {p.finding} [Link]({p.url})")
                    else:
                        lines.append(f"  [Link]({p.url})")
                lines.append("")
        else:
            # Fallback: flat list if no topic groups
            lines.append("---\n")
            lines.append("## Top Journal Papers\n")
            for i, p in enumerate(report.top_papers, 1):
                lines.append(f"{i}. **{p.title}** — {p.abbrev_authors} ({p.journal}) [{p.display_score:.1f}/10]")
                if p.finding:
                    lines.append(f"   {p.finding}")
                lines.append(f"   [Link]({p.url})\n")

            lines.append("---\n## Top Preprints (bioRxiv)\n")
            for i, p in enumerate(report.top_preprints, 1):
                lines.append(f"{i}. **{p.title}** — {p.abbrev_authors} (bioRxiv) [{p.display_score:.1f}/10]")
                if p.finding:
                    lines.append(f"   {p.finding}")
                lines.append(f"   [Link]({p.url})\n")

        lines.append("---\n## Journal Highlights\n")
        for toc in impact_sort(report.journal_tocs):
            if toc.papers:
                lines.append(f"### {toc.journal_name} ({len(toc.papers)} articles)\n")
                jsummary = report.stats.journal_summaries.get(toc.journal_name)
                if jsummary:
                    lines.append(f"*{jsummary}*\n")
                for p in sorted(toc.papers, key=lambda x: x.pub_date, reverse=True):
                    lines.append(f"- [{p.title}]({p.url}) — {p.short_authors} ({p.pub_date})")
                lines.append("")

        return "\n".join(lines)

    def save(
        self,
        report: DigestReport,
        output_dir: Path,
        filename_prefix: str = "digest",
    ) -> tuple[Path, Path]:
        """
        Write HTML and Markdown files to output_dir.
        Returns (html_path, md_path).
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        date_str = report.generated_date.strftime("%Y-%m-%d")

        html_path = output_dir / f"{filename_prefix}_{date_str}.html"
        md_path = output_dir / f"{filename_prefix}_{date_str}.md"

        html_content = self.generate_html(report)
        md_content = self.generate_markdown(report)

        html_path.write_text(html_content, encoding="utf-8")
        md_path.write_text(md_content, encoding="utf-8")

        return html_path, md_path
