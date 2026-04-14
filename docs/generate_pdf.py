"""
Generate a PDF from how-it-works.html using a headless Chromium/Chrome browser.

Requirements (install one of):
    pip install playwright && playwright install chromium
    pip install pyppeteer

Usage:
    cd docs
    python generate_pdf.py

Output: how-it-works.pdf in the same directory.
"""

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
HTML = (HERE / "how-it-works.html").resolve()
PDF  = (HERE / "how-it-works.pdf").resolve()


def try_playwright():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(HTML.as_uri())
        page.pdf(path=str(PDF), format="A4", print_background=True,
                 margin={"top": "10mm", "bottom": "10mm",
                         "left": "10mm", "right": "10mm"})
        browser.close()
    print(f"PDF saved: {PDF}")


def try_chrome_cli():
    """Use system Chrome/Chromium headless mode."""
    for exe in ["google-chrome", "chromium", "chromium-browser",
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"]:
        try:
            subprocess.run(
                [exe, "--headless", "--disable-gpu",
                 f"--print-to-pdf={PDF}",
                 "--print-to-pdf-no-header",
                 str(HTML)],
                check=True, capture_output=True,
            )
            print(f"PDF saved: {PDF}")
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue
    return False


if __name__ == "__main__":
    print(f"Converting {HTML.name} → PDF...")

    # Try playwright first
    try:
        try_playwright()
        sys.exit(0)
    except ImportError:
        pass
    except Exception as exc:
        print(f"Playwright failed: {exc}")

    # Try headless Chrome
    if try_chrome_cli():
        sys.exit(0)

    print("\nCould not generate PDF automatically.")
    print("Manual method (works everywhere):")
    print(f"  1. Open {HTML} in Chrome or Edge")
    print("  2. Press Ctrl+P (Print)")
    print("  3. Destination: Save as PDF")
    print("  4. More settings → Paper size: A4, Margins: Default")
    print("  5. Save")
