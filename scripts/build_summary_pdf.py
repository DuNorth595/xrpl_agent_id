# SPDX-FileCopyrightText: 2026 Justin Douglas
# SPDX-License-Identifier: MIT
"""build_summary_pdf.py — Render docs/00_executive_summary.html → PDF.

Single source of truth for the executive-summary PDF. Reads the existing
hand-crafted HTML (no pandoc needed) and prints it via Playwright.

Usage:
    /usr/bin/python3 scripts/build_summary_pdf.py
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from playwright.async_api import async_playwright


HERE = Path(__file__).parent.parent
HTML_SRC = HERE / "docs" / "00_executive_summary.html"
PDF_OUT = HERE / "docs" / "00_executive_summary.pdf"


async def html_to_pdf(html_path: Path, pdf_path: Path) -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        await page.goto(f"file://{html_path}")
        await page.wait_for_load_state("networkidle")
        await page.pdf(
            path=str(pdf_path),
            format="Letter",
            margin={"top": "0.5in", "bottom": "0.5in",
                    "left": "0.5in", "right": "0.5in"},
            print_background=True,
            prefer_css_page_size=True,
        )
        await browser.close()


def main() -> None:
    if not HTML_SRC.exists():
        raise SystemExit(f"missing source: {HTML_SRC}")
    asyncio.run(html_to_pdf(HTML_SRC, PDF_OUT))
    size_kb = PDF_OUT.stat().st_size / 1024
    print(f"Wrote PDF: {PDF_OUT}  ({size_kb:.1f} KB)")


if __name__ == "__main__":
    main()
