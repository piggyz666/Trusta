"""Render the figures (docs/figures/src/fig*.html → docs/figures/fig*.png, Chinese and English).

    python -m scripts.render_figures
"""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1] / "docs" / "figures"


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for src in sorted((ROOT / "src").glob("fig*.html")):
            for lang, suffix in (("zh", ""), ("en", "-en")):
                page = browser.new_page(device_scale_factor=2, viewport={"width": 1700, "height": 1400})
                page.goto(src.as_uri() + f"?lang={lang}")
                page.wait_for_selector("body[data-ready='1']", timeout=15000)
                page.wait_for_timeout(200)
                page.locator("#fig").screenshot(path=str(ROOT / f"{src.stem}{suffix}.png"))
                page.close()
                print("rendered", src.stem + suffix)
        browser.close()


if __name__ == "__main__":
    main()
