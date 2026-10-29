"""Figure for the comparative evaluation (docs/compare/figure.{png,svg,pdf}), drawn from docs/compare/report.json.

    python -m benchmarks.compare.figure
"""
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import FancyArrowPatch  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "compare"

# Validated categorical palette (dataviz validate_palette.js, light surface): every check passes; marks are direct-labelled.
COLOR = {"A": "#E5704F", "B": "#AE90E0", "C": "#0A6E99"}
NAME = {"A": "裸智能体", "B": "提示词加固 + 确认框", "C": "TRUSTA"}
INK, INK2, MUTED, GRID, SURFACE = "#1F2328", "#4B5563", "#8A9099", "#E6E8EB", "#FFFFFF"
GOOD = "#EAF4F8"


def setup():
    for name in ("Noto Sans CJK SC", "Noto Sans CJK JP", "Noto Sans CJK TC"):
        if any(name == f.name for f in font_manager.fontManager.ttflist):
            break
    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": [name, "DejaVu Sans"], "font.size": 7.5, "axes.unicode_minus": False,
        "axes.linewidth": 0.6, "axes.edgecolor": INK2, "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2,
        "xtick.major.width": 0.6, "ytick.major.width": 0.6, "xtick.major.size": 2.5, "ytick.major.size": 2.5,
        "axes.spines.top": False, "axes.spines.right": False, "svg.fonttype": "none", "pdf.fonttype": 42,
    })


def letter(ax, text, x=-0.02, y=1.0):
    ax.text(x, y, text, transform=ax.transAxes, fontsize=11, fontweight="bold", color=INK, va="bottom", ha="right")


def title(ax, text, sub=None):
    ax.set_title(text, loc="left", fontsize=8.5, fontweight="bold", color=INK, pad=14 if sub else 6)
    if sub:
        ax.text(0, 1.02, sub, transform=ax.transAxes, fontsize=6.8, color=MUTED, va="bottom")


def panel_tradeoff(ax, s):
    """a: the claim in one picture: safety (y) against getting things done in the same turn (x)."""
    pct = lambda v: v * 100
    ax.axhspan(-2, 2, xmin=0.66, xmax=1, color=GOOD, zorder=0, lw=0)
    ax.text(99, -2.5, "理想区域：零越权、当轮办成", ha="right", va="top", fontsize=6.6, color=COLOR["C"])
    for arm in "AB":  # one extra reply ("确认" / 验证码) moves the baselines right; TRUSTA needs none
        x0, x1, y = pct(s[arm]["task_success_same_turn"]), pct(s[arm]["task_success"]), pct(s[arm]["attack_success"])
        ax.add_patch(FancyArrowPatch((x0 + 2.4, y), (x1 - 2.2, y), arrowstyle="-|>", mutation_scale=7, lw=0.8,
                                     color=COLOR[arm], alpha=0.55, zorder=2, linestyle=(0, (3, 2))))
        ax.scatter([x1], [y], s=38, facecolor=SURFACE, edgecolor=COLOR[arm], lw=1.4, zorder=3)
    for arm in "ABC":
        x, y = pct(s[arm]["task_success_same_turn"]), pct(s[arm]["attack_success"])
        ax.scatter([x], [y], s=95 if arm == "C" else 60, color=COLOR[arm], edgecolor=SURFACE, lw=1.6, zorder=4)
    a, b, c = (s[k] for k in "ABC")
    ax.annotate(f"裸智能体\n当轮 {pct(a['task_success_same_turn']):.0f}%，攻击成功 {pct(a['attack_success']):.1f}%",
                (pct(a["task_success_same_turn"]), pct(a["attack_success"])), xytext=(52, 22.5), fontsize=7, color=INK,
                ha="left", va="center", arrowprops=dict(arrowstyle="-", lw=0.5, color=MUTED, shrinkA=2, shrinkB=5))
    ax.annotate(f"提示词加固 + 确认框\n当轮仅 {pct(b['task_success_same_turn']):.0f}%",
                (pct(b["task_success_same_turn"]), 0), xytext=(36, 8.5), fontsize=7, color=INK, ha="center", va="center",
                arrowprops=dict(arrowstyle="-", lw=0.5, color=MUTED, shrinkA=2, shrinkB=5))
    ax.text((pct(b["task_success_same_turn"]) + pct(b["task_success"])) / 2, 0.9, "客户再回复一轮", fontsize=6.3, color=MUTED,
            ha="center", va="bottom")
    ax.annotate(f"TRUSTA\n当轮 {pct(c['task_success_same_turn']):.0f}%，攻击成功 0%",
                (pct(c["task_success_same_turn"]), 0), xytext=(92, 12.5), fontsize=7.5, color=INK, fontweight="bold",
                ha="center", va="center", arrowprops=dict(arrowstyle="-", lw=0.6, color=COLOR["C"], shrinkA=2, shrinkB=6))
    ax.set_xlim(25, 100)
    ax.set_ylim(-5, 27)
    ax.set_xlabel("正常业务当轮完成率（%）  →  越高越好")
    ax.set_ylabel("攻击成功率（%）  →  越低越好")
    ax.grid(True, color=GRID, lw=0.5)
    ax.set_axisbelow(True)
    ax.scatter([], [], s=30, facecolor=SURFACE, edgecolor=MUTED, lw=1.2, label="空心：客户再回复一次“确认”或验证码之后")
    ax.legend(loc="upper left", frameon=False, fontsize=6.5, handletextpad=0.4, borderaxespad=0.2)
    title(ax, "安全与好用不必二选一", "同一个模型、同一个银行、同一份题库，只换“智能体怎么搭”")


def hbars(ax, rows, fmt, xmax, xlabel):
    """Grouped horizontal bars, one group per row, arms in fixed order A, B, C; zero values get an explicit marker."""
    height, gap = 0.22, 0.03
    labels = []
    for i, (label, values) in enumerate(rows):
        labels.append((len(rows) - 1 - i, label))
        for j, arm in enumerate("ABC"):
            y = len(rows) - 1 - i + (1 - j) * (height + gap)
            v = values[arm]
            if v > 0:
                ax.barh(y, v, height=height, color=COLOR[arm], lw=0, zorder=2)
            else:
                ax.plot([0], [y], marker="|", ms=6, mew=1.2, color=COLOR[arm], zorder=3)
            ax.text(v + xmax * 0.015, y, fmt(v), va="center", ha="left", fontsize=6.5, color=INK if v else MUTED)
    ax.set_yticks([p for p, _ in labels])
    ax.set_yticklabels([l for _, l in labels], fontsize=7, color=INK)
    ax.tick_params(axis="y", length=0)
    ax.set_xlim(0, xmax)
    ax.set_ylim(-0.55, len(rows) - 0.45)
    ax.set_xlabel(xlabel)
    ax.grid(True, axis="x", color=GRID, lw=0.5)
    ax.set_axisbelow(True)


def main():
    setup()
    s = json.loads((OUT / "report.json").read_text(encoding="utf-8"))["summary"]
    meta = json.loads((OUT / "report.json").read_text(encoding="utf-8"))
    fig = plt.figure(figsize=(7.2, 5.0), dpi=300, facecolor=SURFACE)
    grid = fig.add_gridspec(3, 2, width_ratios=[1.25, 1], height_ratios=[1.2, 0.85, 0.55], left=0.075, right=0.955,
                            top=0.80, bottom=0.12, wspace=0.42, hspace=1.0)
    a = fig.add_subplot(grid[:, 0])
    b = fig.add_subplot(grid[0, 1])
    c = fig.add_subplot(grid[1, 1])
    d = fig.add_subplot(grid[2, 1])

    panel_tradeoff(a, s)
    letter(a, "a", x=-0.06)

    pct = lambda v: v * 100
    hbars(b, [("直接注入", {k: pct(s[k]["direct_success"]) for k in "ABC"}),
              ("间接注入", {k: pct(s[k]["indirect_success"]) for k in "ABC"}),
              ("高风险操作", {k: pct(s[k]["privilege_success"]) for k in "ABC"})],
          lambda v: f"{v:.1f}%" if v else "0", 80, "攻击成功率（%）")
    title(b, "按攻击类型", "间接注入：这一代模型本身已能识破，三组都是 0")
    letter(b, "b", x=-0.25)

    hbars(c, [("未经确认就执行", {k: s[k]["unconfirmed_writes"] for k in "ABC"}),
              ("泄露系统提示词", {k: s[k]["prompt_leaks"] for k in "ABC"})],
          lambda v: f"{v:.0f} 次" if v else "0", 15, "")
    c.set_xticks([0, 5, 10, 15])
    title(c, "实际出事的次数", "每组 654 次对话中")
    letter(c, "c", x=-0.25)

    hbars(d, [("强验证覆盖率", {k: pct(s[k]["strong_auth_coverage"] or 0) for k in "ABC"})],
          lambda v: "", 100, "覆盖率（%）")
    d.set_xticks([0, 25, 50, 75, 100])
    d.text(1.5, 0.25, "0", fontsize=6.5, color=MUTED, va="center")
    d.text(2, 0.0, f"{pct(s['B']['strong_auth_coverage']):.1f}% · 靠模型自觉遵守提示词", fontsize=6.5, color=INK, va="center")
    d.text(2, -0.25, "100% · 由系统强制执行", fontsize=6.5, color=SURFACE, va="center", fontweight="bold")
    title(d, "高风险操作的强验证", "大额、新收款人、挂失、改密、理财申购")
    letter(d, "d", x=-0.25)

    handles = [plt.Line2D([0], [0], marker="o", ls="", ms=6, mfc=COLOR[k], mec=SURFACE, label=NAME[k]) for k in "ABC"]
    fig.legend(handles=handles, loc="upper left", bbox_to_anchor=(0.068, 0.905), ncol=3, frameon=False, fontsize=7.4,
               handletextpad=0.3, columnspacing=1.6)
    fig.text(0.075, 0.96, "同一个大模型，三种搭法：只有 TRUSTA 同时做到零越权和当轮办成", fontsize=11, fontweight="bold", color=INK)
    runs = sum(s[k]["runs"] for k in "ABC")
    fig.text(0.075, 0.925, f"模型 {meta['meta'].get('model', '')} · 218 题（正常业务、模糊指令、直接 / 间接注入、诱导编造、高风险操作）"
                           f" × 3 组 × 3 次 = {runs:,} 次对话", fontsize=6.8, color=MUTED)
    fig.text(0.075, 0.02, f"题库 SHA-256 {meta['sha256'][:16]}… · 复现：python -m benchmarks.compare --live --runs 3 · "
                           "方法与逐条记录：docs/compare", fontsize=6, color=MUTED)
    for ext in ("png", "svg", "pdf"):
        fig.savefig(OUT / f"figure.{ext}", facecolor=SURFACE)
    print("\n".join(str(OUT / f"figure.{ext}") for ext in ("png", "svg", "pdf")))


if __name__ == "__main__":
    main()
