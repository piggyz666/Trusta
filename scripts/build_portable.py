"""Build the Windows portable package: TRUSTA-windows-x64.zip (unzip and double-click TRUSTA.cmd).

The package carries its own Python (the official "embeddable" distribution), so users install nothing.
Runs on any OS (CI uses Linux): dependencies are fetched as Windows wheels with pip's --platform.

    python -m scripts.build_portable --python-embed python-3.12.7-embed-amd64.zip --out dist
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Only what the running product needs; development material stays out of the download.
EXCLUDE_TOP = {"tests", "docs", "frontend-app", ".github", "anchor-integration", "llm-semantic-convergence"}
EXCLUDE_FILES = re.compile(r"(^|/)(\.gitignore|\.gitattributes|install\.sh|start\.sh|start-revolut\.ps1|TRUSTA_.*\.md)$")


def tracked_files():
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True).stdout.decode()
    for rel in filter(None, out.split("\0")):
        if rel.split("/")[0] in EXCLUDE_TOP or EXCLUDE_FILES.search(rel):
            continue
        yield rel


def build(embed_zip: Path, out_dir: Path, py_version: str, name: str) -> Path:
    stage = out_dir / "stage" / "TRUSTA"
    if stage.parent.exists():
        shutil.rmtree(stage.parent)
    stage.mkdir(parents=True)

    for rel in tracked_files():
        dst = stage / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, dst)

    runtime = stage / "runtime"
    pydir = runtime / "python"
    with zipfile.ZipFile(embed_zip) as z:
        z.extractall(pydir)
    pth = next(pydir.glob("python3*._pth"), None)
    if pth is None:
        raise SystemExit("embeddable zip has no python3*._pth file")
    # import path: stdlib zip, the runtime dir and bundled packages. The TRUSTA folder itself is added by
    # sitecustomize.py (below): on Windows a "../.." line here resolved one level short (runtime instead of TRUSTA).
    zips = [p.name for p in pydir.glob("python3*.zip")]
    pth.write_text("\n".join(zips + [".", "../site-packages", "import site", ""]), encoding="utf-8")

    site = runtime / "site-packages"
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--no-compile", "--target", str(site),
                    "--platform", "win_amd64", "--python-version", py_version, "--implementation", "cp",
                    "--only-binary=:all:", "-r", str(ROOT / "requirements.txt"), "-r", str(ROOT / "requirements-channels.txt")],
                   check=True)
    # Embedded Python imports sitecustomize at start-up ("import site" in the ._pth); it puts the TRUSTA folder
    # (runtime/site-packages/../..) on sys.path so `python -m backend.server` works from any working directory.
    (site / "sitecustomize.py").write_text(
        "import os, sys\n"
        "_root = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))\n"
        "if _root not in sys.path:\n"
        "    sys.path.append(_root)\n", encoding="utf-8")

    (stage / "README-先读我.txt").write_text(
        "TRUSTA 便携版（Windows x64）\r\n\r\n"
        "1. 双击 TRUSTA.cmd。首次运行会打开配置向导（大模型、银行、访问方式、安全），全部可以直接回车用默认值。\r\n"
        "2. 窗口里会打印 http://localhost:8000/#token=… ，用浏览器打开即可。\r\n"
        "3. 关闭窗口即停止 TRUSTA。数据保存在本文件夹的 data\\ 里。\r\n\r\n"
        "已自带 Python，不需要另外安装任何东西。\r\n", encoding="utf-8")

    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"{name}.zip"
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(stage.parent))
    shutil.rmtree(stage.parent)
    return archive


def main():
    parser = argparse.ArgumentParser(description="Build the TRUSTA Windows portable zip")
    parser.add_argument("--python-embed", type=Path, required=True, help="python-3.x.y-embed-amd64.zip from python.org")
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    parser.add_argument("--python-version", default="3.12")
    parser.add_argument("--name", default="TRUSTA-windows-x64")
    args = parser.parse_args()
    archive = build(args.python_embed, args.out, args.python_version, args.name)
    print(f"{archive}  {os.path.getsize(archive) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
