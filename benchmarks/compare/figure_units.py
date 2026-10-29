"""Unit chart: every dot is one evaluated conversation (654 per arm), coloured by what happened.

    python -m benchmarks.compare.figure_units      -> docs/compare/figure-units.{png,svg,pdf}
"""
import json
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "compare"

# three type sizes only
BIG, MID, SMALL = 11, 8, 6.8
INK, INK2, MUTED, SURFACE = "#1F2328", "#4B5563", "#8A9099", "#FFFFFF"
# outcome colours (validated): correct is a quiet tint so that what went wrong stands out
OUTCOMES = [  # (key, label, face, edge)
    ("harm", "出事：越权执行 / 泄露提示词 / 高风险未强验证", "#C8372D", "#C8372D"),
    ("extra", "客户要再回复一轮才办成", "#E39A2A", "#E39A2A"),
    ("fail", "没办成（但没办错）", "#FFFFFF", "#AEB4BB"),
    ("ok", "处理正确（含该拒绝的拒绝、该追问的追问）", "#A9CBE2", "#A9CBE2"),
]
ARMS = [("A", "裸智能体", "模型直接调银行接口"), ("B", "提示词加固 + 确认框", "8 条安全规则，写操作弹确认"),
        ("C", "TRUSTA", "本作品")]


def outcome(record):
    v, category = record["verdict"], record["category"]
    if v["breached"] or (category == "normal" and v["weak_auth"]):
        return "harm"
    if category in ("normal", "ambiguous"):
        if not v["task_ok"]:
            return "fail"
        if not v.get("task_ok_same_turn", v["task_ok"]):
            return "extra"
    return "ok"


def font():
    for name in ("Noto Sans CJK SC", "Noto Sans CJK JP"):
        if any(name == f.name for f in font_manager.fontManager.ttflist):
            return name
    return "DejaVu Sans"


def main():
    plt.rcParams.update({"font.family": "sans-serif", "font.sans-serif": [font(), "DejaVu Sans"], "svg.fonttype": "none",
                         "pdf.fonttype": 42, "axes.unicode_minus": False})
    records = [json.loads(line) for line in (OUT / "runs.jsonl").read_text(encoding="utf-8").splitlines()]
    meta = json.loads((OUT / "report.json").read_text(encoding="utf-8"))
    counts = {arm: Counter(outcome(r) for r in records if r["arm"] == arm) for arm, _, _ in ARMS}
    cols = 26
    fig = plt.figure(figsize=(7.2, 4.2), dpi=300, facecolor=SURFACE)
    fig.text(0.04, 0.955, "1,962 次对话，每个点是一次：只有 TRUSTA 一次也没出事，而且当轮就办成", fontsize=BIG, fontweight="bold",
             color=INK, va="top")
    fig.text(0.04, 0.895, f"同一个模型（{meta['meta'].get('model', '')}）、同一个沙箱银行、同一份 218 道题，每题每组 3 次；"
                          "题目包括正常业务、模糊指令、直接注入、间接注入、诱导编造、高风险操作", fontsize=SMALL, color=MUTED, va="top")
    width, left, gap = 0.29, 0.04, 0.035
    for i, (arm, name, note) in enumerate(ARMS):
        x0 = left + i * (width + gap)
        ax = fig.add_axes([x0, 0.255, width, 0.54])
        ax.set_axis_off()
        order = [key for key, *_ in OUTCOMES]
        dots = [key for key in order for _ in range(counts[arm][key])]
        rows = -(-len(dots) // cols)
        for n, key in enumerate(dots):
            face, edge = next((f, e) for k, _, f, e in OUTCOMES if k == key)
            ax.scatter(n % cols, -(n // cols), s=9.5, facecolor=face, edgecolor=edge, linewidth=0.6)
        ax.set_xlim(-0.8, cols - 0.2)
        ax.set_ylim(-rows + 0.2, 0.8)
        ax.set_aspect("equal")
        harm = counts[arm]["harm"]
        fig.text(x0, 0.835, name, fontsize=MID, fontweight="bold", color=INK, va="bottom")
        fig.text(x0, 0.808, note, fontsize=SMALL, color=MUTED, va="top")
        line = [("出事", harm, "#C8372D"), ("再回复一轮", counts[arm]["extra"], "#B8740F"), ("没办成", counts[arm]["fail"], INK2)]
        y = 0.215
        for label, value, color in line:
            fig.text(x0, y, label, fontsize=SMALL, color=INK2, va="center")
            fig.text(x0 + width * 0.42, y, f"{value}", fontsize=MID, fontweight="bold", color=color if value else MUTED,
                     va="center", ha="right")
            y -= 0.045
        fig.text(x0 + width * 0.52, 0.215, "处理正确", fontsize=SMALL, color=INK2, va="center")
        fig.text(x0 + width, 0.215, f"{counts[arm]['ok']}/654", fontsize=MID, fontweight="bold", color=INK, va="center", ha="right")
    # legend
    x = 0.04
    for key, label, face, edge in OUTCOMES:
        fig.add_artist(plt.Line2D([x + 0.004], [0.055], marker="o", ms=4.2, mfc=face, mec=edge, mew=0.8, ls="",
                                  transform=fig.transFigure))
        t = fig.text(x + 0.013, 0.055, label, fontsize=SMALL, color=INK2, va="center")
        fig.canvas.draw()
        x += t.get_window_extent().width / fig.bbox.width + 0.022
    fig.text(0.04, 0.012, f"题库 SHA-256 {meta['sha256'][:16]}… · 复现：python -m benchmarks.compare --live --runs 3 · docs/compare",
             fontsize=5.8, color=MUTED, va="bottom")
    for ext in ("png", "svg", "pdf"):
        fig.savefig(OUT / f"figure-units.{ext}", facecolor=SURFACE)
    print(counts)


if __name__ == "__main__":
    main()
