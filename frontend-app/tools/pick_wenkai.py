"""Copy only the LXGW WenKai web-font chunks the landing page needs into public/fonts/wenkai/.

The full font is ~30 MB split into unicode-range chunks; the page uses a few hundred characters.
Re-run after changing landing copy:  python tools/pick_wenkai.py
"""
import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PKG = ROOT / "node_modules" / "lxgw-wenkai-webfont"
OUT = ROOT / "public" / "fonts" / "wenkai"
SOURCES = [ROOT / "src" / "intro" / "copy.js"]

def ranges(spec):
    for part in spec.split(","):
        part = part.strip().replace("U+", "").replace("u+", "")
        if "-" in part:
            a, b = part.split("-"); yield int(a, 16), int(b, 16)
        elif part:
            v = int(part, 16); yield v, v

def main():
    text = "".join(p.read_text(encoding="utf-8") for p in SOURCES)
    needed = {ord(ch) for ch in text if ord(ch) > 0x7F} | set(range(0x20, 0x7F))
    css = (PKG / "lxgwwenkai-regular.css").read_text(encoding="utf-8")
    blocks = re.findall(r"@font-face\s*{[^}]*}", css)
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)
    keep, size = [], 0
    for block in blocks:
        m = re.search(r"unicode-range:\s*([^;}]+)", block)
        if not m or not any(a <= c <= b for c in needed for a, b in ranges(m.group(1))):
            continue
        url = re.search(r"url\(['\"]?([^'\")]+\.woff2)['\"]?\)", block).group(1)
        src = PKG / url.lstrip("./")
        shutil.copy2(src, OUT / src.name)
        size += src.stat().st_size
        block = re.sub(r"font-family:\s*['\"][^'\"]+['\"]", "font-family: 'TRUSTA WenKai'", block)
        block = re.sub(r"url\(['\"]?[^'\")]+\.woff2['\"]?\)", f"url('/fonts/wenkai/{src.name}')", block)
        if "font-display" not in block:
            block = block.replace("{", "{ font-display: swap;", 1)
        keep.append(block)
    (OUT / "wenkai.css").write_text("\n".join(keep) + "\n", encoding="utf-8")
    print(f"{len(keep)} chunks, {size / 1024:.0f} KB for {len(needed)} characters")

if __name__ == "__main__":
    main()
