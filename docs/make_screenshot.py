"""Capture docs/screenshot.png from a running dashboard.

Development helper, not part of the application. Needs Selenium and a
local Chrome; Selenium downloads a matching driver by itself::

    pip install selenium
    python -m datasheet_extractor.dashboard &
    python docs/make_screenshot.py http://127.0.0.1:8050
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options

OUT = Path(__file__).parent / "screenshot.png"


def main(url: str) -> None:
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1280,1150")
    options.add_argument("--hide-scrollbars")
    driver = webdriver.Chrome(options=options)
    try:
        driver.get(url)
        time.sleep(4)  # let Dash render the table and chart
        driver.save_screenshot(str(OUT))
        print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB)")
    finally:
        driver.quit()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8050")
