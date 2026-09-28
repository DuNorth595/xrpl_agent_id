# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""build_pdf.py — Render xrpl_agent_id README into a professional Letter PDF.

Pipeline (matches the proven daily-brief template used for AI Intel Briefs):
  1. pandoc        → README.md → standalone HTML (with embedded print CSS)
  2. Playwright    → HTML → PDF (Letter, 0.5in margins, print_background)

pandoc handles every Markdown edge case correctly: tables, nested lists,
fenced code blocks, blockquotes, inline formatting. We don't try to roll
our own MD→HTML converter.

Usage:
    /usr/bin/python3 build_pdf.py
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path

from playwright.async_api import async_playwright


HERE = Path(__file__).parent
README_MD = HERE / "README.md"
HTML_OUT = HERE / "xrpl_agent_id_README.html"
PDF_OUT = HERE / "xrpl_agent_id_README.pdf"


# ---------- Print-quality CSS injected via pandoc ----------
# Tailored for a technical README on Letter paper, 0.5in margins.
# Adapts the canonical daily-brief palette (light, paper-white, XRPL-blue
# accent) and adds code-block, table, and list rules that work for a
# library reference doc.
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
  font-size: 28pt !important;
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

/* First H1 in the document is the README title — our cover banner
   already shows it, so hide it in the rendered PDF body. Pandoc emits
   the first H1 with id="xrpl_agent_id" matching the title text. */
#xrpl_agent_id { display: none; }

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


# HTML inserted at the top of <body> — cover banner + tagline + meta
COVER_HTML = """
<div class="cover">
  <h1 class="title">xrpl_agent_id</h1>
  <div class="tagline">Identity for AI agents on the XRP Ledger</div>
  <div class="meta">MIT License · Python 3.9+ · xrpl-py 4.5+ · REUSE Compliant</div>
</div>
"""

# HTML inserted at the bottom of <body> — footer line
FOOTER_HTML = """
<div class="footer">
  xrpl_agent_id v0.2.0 — beta — generated from README.md
</div>
"""


# ---------- pandoc → HTML ----------
def run_pandoc() -> str:
    """Run pandoc on README.md with our print CSS injected. Returns HTML text."""
    # Write the three includes to real files. Pandoc's --include-in-header
    # injects raw HTML into <head>, so the CSS must be wrapped in <style>.
    css_path = HERE / "_print_css.html"
    cover_path = HERE / "_cover.html"
    footer_path = HERE / "_footer.html"
    css_path.write_text(f"<style>{PRINT_CSS}</style>", encoding="utf-8")
    cover_path.write_text(COVER_HTML, encoding="utf-8")
    footer_path.write_text(FOOTER_HTML, encoding="utf-8")

    cmd = [
        "pandoc",
        str(README_MD),
        "--standalone",                  # complete HTML doc
        "--from", "gfm",                  # GitHub-flavored markdown (tables)
        "--to", "html5",
        "--wrap=preserve",
        "--no-highlight",
        "--include-in-header", str(css_path),
        "--include-before-body", str(cover_path),
        "--include-after-body", str(footer_path),
        "-o", str(HTML_OUT),
    ]
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError(f"pandoc failed:\nSTDOUT: {proc.stdout}\nSTDERR: {proc.stderr}")
    return HTML_OUT.read_text(encoding="utf-8")


# ---------- Playwright → PDF ----------
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


# ---------- Main ----------
def main() -> None:
    html_text = run_pandoc()
    print(f"Wrote HTML: {HTML_OUT}  ({len(html_text)} bytes)")

    asyncio.run(html_to_pdf(HTML_OUT, PDF_OUT))
    size_kb = PDF_OUT.stat().st_size / 1024
    print(f"Wrote PDF:  {PDF_OUT}  ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
