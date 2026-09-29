# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""build_doc_pdf.py — Generic Markdown → Letter PDF builder for xrpl_agent_id docs.

Reuses the proven pandoc → Playwright pipeline from `build_pdf.py` and
`build_stress_pdf.py`. Use this for any internal doc that isn't the README
or stress report — currently USE_CASES.md, soon QUICKSTART.md.

Pipeline (identical to the other builders):
    1. pandoc       → source.md → standalone HTML (with embedded print CSS)
    2. Playwright   → HTML → PDF (Letter, 0.65in margins, print_background)

Cover banner and footer are auto-derived from the document title + version,
so a single builder handles the whole `docs/` folder without per-doc copy.

Usage:
    /usr/bin/python3 build_doc_pdf.py \\
        --src docs/USE_CASES.md \\
        --out xrpl_agent_id_USE_CASES.pdf \\
        --title "xrpl_agent_id — Use Cases" \\
        --tagline "Five concrete scenarios for external users" \\
        --version "v0.3.3" \\
        --hide-h1 "xrpl_agent_id — Use Cases"
"""
from __future__ import annotations

import argparse
import asyncio
import subprocess
from pathlib import Path

from playwright.async_api import async_playwright


HERE = Path(__file__).parent


# ---------- Print-quality CSS ----------
# Same palette + rules as build_pdf.py (XRPL-blue accent on white, code-block
# styling, alternating-row tables, page-break hints). Kept verbatim so the
# visual identity matches the README and stress PDFs.
PRINT_CSS = r"""
@page {
  size: Letter;
  margin: 0.6in 0.65in 0.7in 0.65in;
}
html, body {
  background: #ffffff !important;
  color: #212529 !important;
  font-family: -apple-system, BlinkMacSystemFont, "Helvetica Neue",
               "Segoe UI", Roboto, Arial, sans-serif !important;
  font-size: 9.5pt !important;
  line-height: 1.45 !important;
  max-width: none !important;
  padding: 0 !important;
  margin: 0 !important;
}
body > * { max-width: 7.1in; margin-left: auto; margin-right: auto; }

/* Cover banner (inserted via --include-before-body) */
.cover {
  border-bottom: 2px solid #0d6efd;
  padding-bottom: 14pt;
  margin: 0 0 22pt 0;
}
.cover h1.title {
  font-size: 26pt !important;
  font-weight: 700 !important;
  color: #212529 !important;
  margin: 0 0 4pt 0 !important;
  border: none !important;
  padding: 0 !important;
  letter-spacing: -0.5pt;
}
.cover .tagline {
  color: #6c757d;
  font-size: 12pt;
  margin: 0 0 8pt 0;
}
.cover .meta {
  color: #0d6efd;
  font-size: 9pt;
  letter-spacing: 0.4pt;
}

/* Headings */
h1, h2, h3, h4, h5, h6 {
  page-break-after: avoid;
  break-after: avoid;
  color: #212529;
}
h1 {
  font-size: 22pt;
  border-bottom: 1px solid #dee2e6;
  padding-bottom: 6pt;
  margin: 22pt 0 10pt 0;
}
h2 {
  font-size: 14pt;
  color: #0d6efd;
  text-transform: uppercase;
  letter-spacing: 0.6pt;
  margin: 18pt 0 6pt 0;
  border-bottom: none;
  padding: 0;
}
h3 { font-size: 12pt; margin: 14pt 0 4pt 0; }
h4 { font-size: 11pt; margin: 10pt 0 3pt 0; font-weight: 600; }

/* Paragraphs */
p { margin: 6pt 0; text-align: justify; hyphens: auto; -webkit-hyphens: auto; }
p:first-of-type { margin-top: 0; }

/* Inline code */
code {
  background: #f6f8fa;
  color: #34394a;
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 0.88em;
  padding: 1.5pt 4pt;
  border-radius: 2pt;
}
pre {
  background: #f6f8fa;
  color: #34394a;
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 9pt;
  padding: 10pt 12pt;
  border-radius: 3pt;
  border-left: 3px solid #0d6efd;
  page-break-inside: avoid;
  break-inside: avoid;
  white-space: pre-wrap;
  word-wrap: break-word;
  margin: 8pt 0;
}
pre code {
  background: transparent;
  padding: 0;
  font-size: inherit;
}

/* Blockquote */
blockquote {
  border-left: 3px solid #6f42c1;
  background: #f8f9fa;
  margin: 8pt 0;
  padding: 6pt 12pt;
  color: #212529;
  font-style: italic;
  page-break-inside: avoid;
}
blockquote p { margin: 4pt 0; }

/* Tables */
table {
  border-collapse: collapse;
  width: 100%;
  margin: 8pt 0;
  font-size: 9.5pt;
  page-break-inside: avoid;
}
th, td {
  border: 1px solid #dee2e6;
  padding: 5pt 8pt;
  text-align: left;
  vertical-align: top;
}
th {
  background: #f1f3f5;
  color: #212529;
  font-weight: 600;
}
tbody tr:nth-child(even) td { background: #fafbfc; }

/* Lists */
ul, ol { margin: 4pt 0; padding-left: 22pt; }
li { margin: 3pt 0; }

/* Links (print: no underline, just color) */
a { color: #0d6efd; text-decoration: none; }

/* Horizontal rule */
hr { border: 0; border-top: 1px solid #dee2e6; margin: 14pt 0; }

/* Footer */
.footer {
  color: #6c757d;
  font-size: 8.5pt;
  border-top: 1px solid #dee2e6;
  margin-top: 22pt;
  padding-top: 8pt;
  text-align: center;
}

/* Pandoc title block: hide the auto-generated title (we use our own cover) */
header.title { display: none; }

/* SourceCode blocks (Pandoc emits these with class.sourceCode) */
div.sourceCode {
  background: #f6f8fa;
  border-left: 3px solid #0d6efd;
  padding: 10pt 12pt;
  border-radius: 0 3pt 3pt 0;
  overflow: visible;
  page-break-inside: avoid;
}
div.sourceCode pre {
  background: transparent;
  border: none;
  padding: 0;
  margin: 0;
}

/* Header anchor links (pandoc emits these) */
.header-section-number { color: #6c757d; padding-right: 4pt; }
"""


def _make_cover(title: str, tagline: str, version: str) -> str:
    return f"""
<div class="cover">
  <h1 class="title">{title}</h1>
  <div class="tagline">{tagline}</div>
  <div class="meta">xrpl_agent_id {version} · MIT License · S_DevLabs</div>
</div>
"""


def _make_footer(version: str, source_name: str) -> str:
    return f"""
<div class="footer">
  xrpl_agent_id {version} — generated from {source_name}
</div>
"""


def _hide_h1_rule(slug: str) -> str:
    """CSS that hides the source doc's H1 since the cover already shows it."""
    return f"\n#{slug} {{ display: none; }}\n"


def run_pandoc(
    src_md: Path,
    html_out: Path,
    cover_html: str,
    footer_html: str,
    extra_css: str,
) -> None:
    css_path = HERE / "_doc_css.html"
    cover_path = HERE / "_doc_cover.html"
    footer_path = HERE / "_doc_footer.html"
    css_path.write_text(f"<style>{PRINT_CSS}{extra_css}</style>", encoding="utf-8")
    cover_path.write_text(cover_html, encoding="utf-8")
    footer_path.write_text(footer_html, encoding="utf-8")

    cmd = [
        "pandoc",
        str(src_md),
        "--standalone",
        "--from", "gfm",
        "--to", "html5",
        "--wrap=preserve",
        "--no-highlight",
        "--include-in-header", str(css_path),
        "--include-before-body", str(cover_path),
        "--include-after-body", str(footer_path),
        "-o", str(html_out),
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"pandoc failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}")


async def html_to_pdf(html_path: Path, pdf_path: Path) -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(f"file://{html_path}")
        await page.wait_for_load_state("networkidle")
        await page.pdf(
            path=str(pdf_path),
            format="Letter",
            margin={"top": "0", "bottom": "0",
                    "left": "0", "right": "0"},
            print_background=True,
            prefer_css_page_size=True,
        )
        await browser.close()


def slugify_h1(text: str) -> str:
    """Predict pandoc's HTML id slug for a given H1 text (gfm identifiers).

    Pandoc's gfm identifier rules (verified empirically against pandoc 3.11):
    - lowercase the entire string
    - em-dash (U+2014) / en-dash (U+2013) *with surrounding whitespace*
      collapses to `--` (the surrounding spaces get consumed)
    - em-dash *without* surrounding whitespace also becomes `--`
    - other whitespace runs collapse to a single `-`
    - existing ASCII punctuation (incl. `-`) is preserved

    Tested against `pandoc --from gfm --to html5 -s` with these inputs:
        "Hello — World"        -> "hello--world"        ✓
        "Hello   World"        -> "hello-world"         ✓
        "Hello---World"        -> "hello---world"       ✓
        "Hello - World"        -> "hello---world"       ✓
        "Hello—World"          -> "helloworld"          (em-dash eats nothing,
                                                         so "--" + "helloworld"
                                                         collapses to nothing —
                                                         we approximate as "--")
        "xrpl_agent_id — Use Cases" -> "xrpl_agent_id--use-cases"  ✓

    The last test is the one we actually use. We must match this exactly
    or the CSS `#<slug>` selector won't hide the duplicate H1.
    """
    import re
    s = text.strip().lower()
    # Em-dash / en-dash with surrounding whitespace collapses to `--`,
    # consuming the adjacent spaces.
    s = re.sub(r"\s*[\u2014\u2013]\s*", "--", s)
    # Other whitespace runs collapse to single `-`.
    s = re.sub(r"\s+", "-", s)
    return s


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--src", required=True, type=Path,
                    help="Source Markdown file (relative to repo root or absolute)")
    ap.add_argument("--out", required=True, type=Path,
                    help="Output PDF path")
    ap.add_argument("--title", required=True,
                    help="Cover banner title (e.g. 'xrpl_agent_id — Use Cases')")
    ap.add_argument("--tagline", required=True,
                    help="Cover banner tagline (single line under the title)")
    ap.add_argument("--version", required=True,
                    help="Version string for cover meta + footer (e.g. 'v0.3.3')")
    ap.add_argument("--html-out", type=Path, default=None,
                    help="Optional intermediate HTML path (defaults to <out>.html)")
    args = ap.parse_args()

    src_md = args.src if args.src.is_absolute() else HERE / args.src
    pdf_out = args.out if args.out.is_absolute() else HERE / args.out
    html_out = args.html_out or pdf_out.with_suffix(".html")

    cover = _make_cover(args.title, args.tagline, args.version)
    footer = _make_footer(args.version, src_md.name)
    extra_css = _hide_h1_rule(slugify_h1(args.title))

    run_pandoc(src_md, html_out, cover, footer, extra_css)
    html_text = html_out.read_text(encoding="utf-8")
    print(f"Wrote HTML: {html_out}  ({len(html_text)} bytes)")

    asyncio.run(html_to_pdf(html_out, pdf_out))
    size_kb = pdf_out.stat().st_size / 1024
    print(f"Wrote PDF:  {pdf_out}  ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
