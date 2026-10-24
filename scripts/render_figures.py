"""Render the README figures (docs/figures/src/*.html → docs/figures/*.png). Usage: python -m scripts.render_figures"""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1] / "docs" / "figures"


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for src in sorted((ROOT / "src").glob("fig*.html")):
            page = browser.new_page(device_scale_factor=2, viewport={"width": 1500, "height": 1200})
            page.goto(src.as_uri())
            page.wait_for_timeout(400)
            page.locator("#fig svg").screenshot(path=str(ROOT / (src.stem + ".png")))
            page.close()
            print("rendered", src.stem)
        browser.close()


if __name__ == "__main__":
    main()
