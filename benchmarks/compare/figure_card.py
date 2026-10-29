"""Comparison card: plain-language questions as rows, the three ways of building the agent as columns.

    python -m benchmarks.compare.figure_card       -> docs/compare/figure-card.{png,svg,pdf}
"""
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.patches import Circle, FancyBboxPatch, Rectangle  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "compare"

TITLE, NUMBER, BODY, NOTE = 12, 12, 7.5, 6.4
INK, INK2, MUTED, LINE, TRACK, SURFACE = "#1F2328", "#4B5563", "#8A9099", "#E3E6EA", "#EEF0F2", "#FFFFFF"
GOOD, WARN, BAD = "#2E8B57", "#D98A1E", "#C8372D"  # status colours, always with a ✓ / ! / ✗ glyph
TRUSTA, TRUSTA_TINT = "#0A6E99", "#EEF5F9"


def font():
    for name in ("Noto Sans CJK SC", "Noto Sans CJK JP"):
        if any(name == f.name for f in font_manager.fontManager.ttflist):
            return name
    return "DejaVu Sans"


def main():
    plt.rcParams.update({"font.family": "sans-serif", "font.sans-serif": [font(), "DejaVu Sans"], "svg.fonttype": "none",
                         "pdf.fonttype": 42, "axes.unicode_minus": False})
    report = json.loads((OUT / "report.json").read_text(encoding="utf-8"))
    s = report["summary"]
    pct = lambda v: (v or 0) * 100
    incidents = {k: s[k]["unconfirmed_writes"] + s[k]["prompt_leaks"] + s[k]["unsuitable_sales"] for k in "ABC"}

    # (question, how to read it, scale max, formatter, {arm: (value, verdict, example)})
    rows = [
        ("会不会被话术骗着越权？", "攻击成功率，越低越好", 100, lambda v: f"{v:.1f}%" if v else "0%", {
            "A": (pct(s["A"]["attack_success"]), "bad", "说一句“我已在柜台验证过”，就直接挂失"),
            "B": (pct(s["B"]["attack_success"]), "good", ""),
            "C": (pct(s["C"]["attack_success"]), "good", "")}),
        ("真的出过事吗？", "越权执行 + 泄露系统提示词 + 不适当销售，次数", 20, lambda v: f"{v:.0f} 次", {
            "A": (incidents["A"], "bad", f"{s['A']['unconfirmed_writes']} 次未确认就执行，{s['A']['prompt_leaks']} 次念出系统提示词，{s['A']['unsuitable_sales']} 次卖超风险理财"),
            "B": (incidents["B"], "good", ""),
            "C": (incidents["C"], "good", "")}),
        ("大额、改密等高风险操作，有没有强验证？", "强验证覆盖率，越高越好", 100, lambda v: f"{v:.0f}%" if v in (0, 100) else f"{v:.1f}%", {
            "A": (pct(s["A"]["strong_auth_coverage"]), "bad", "转账、挂失、改密都不验证"),
            "B": (pct(s["B"]["strong_auth_coverage"]), "warn", "靠模型自觉，有 2 次跳过了"),
            "C": (pct(s["C"]["strong_auth_coverage"]), "good", "由系统强制，不靠模型")}),
        ("说一句话，能不能当场办成？", "正常业务当轮完成率，越高越好", 100, lambda v: f"{v:.0f}%", {
            "A": (pct(s["A"]["task_success_same_turn"]), "warn", "办得快，但不设防"),
            "B": (pct(s["B"]["task_success_same_turn"]), "bad", "多数要先口头再问一遍"),
            "C": (pct(s["C"]["task_success_same_turn"]), "good", "")}),
    ]
    arms = [("A", "不设防的智能体", "模型直接调用银行接口"), ("B", "提示词加固", "8 条安全规则 + 确认框"),
            ("C", "TRUSTA", "本作品")]

    fig = plt.figure(figsize=(7.2, 4.3), dpi=300, facecolor=SURFACE)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 60)
    ax.set_axis_off()

    ax.text(3, 57.5, "同一个大模型，三种搭法：只有 TRUSTA 既不出事，又能当场办成", fontsize=TITLE, fontweight="bold", color=INK, va="top")
    runs = sum(s[k]["runs"] for k in "ABC")
    ax.text(3, 53.4, f"同一个模型（{report['meta'].get('model', '')}）、同一个银行、同一份 218 道题，每题每组跑 3 次，共 {runs:,} 次真实对话",
            fontsize=NOTE, color=MUTED, va="top")

    label_w, col_x, col_w = 31, [35, 56.5, 78], 19.5
    top, row_h = 44.5, 9.0
    # TRUSTA column highlight
    ax.add_patch(FancyBboxPatch((col_x[2] - 1.2, top - 4 * row_h - 1.2), col_w + 2.4, 4 * row_h + 8.2,
                                boxstyle="round,pad=0,rounding_size=1.2", fc=TRUSTA_TINT, ec="none", zorder=0))
    for (arm, name, note), x in zip(arms, col_x):
        ax.text(x, top + 4.6, name, fontsize=BODY + 1, fontweight="bold", color=TRUSTA if arm == "C" else INK, va="center")
        ax.text(x, top + 2.0, note, fontsize=NOTE, color=MUTED, va="center")

    for i, (question, how, scale, fmt, cells) in enumerate(rows):
        y = top - i * row_h
        ax.plot([3, 97], [y, y], color=LINE, lw=0.6, zorder=1)
        ax.text(3, y - 3.2, question, fontsize=BODY + 0.5, fontweight="bold", color=INK, va="center")
        ax.text(3, y - 6.2, how, fontsize=NOTE, color=MUTED, va="center")
        for arm, x in zip("ABC", col_x):
            value, verdict, example = cells[arm]
            color = {"good": GOOD, "warn": WARN, "bad": BAD}[verdict]
            glyph = {"good": "✓", "warn": "!", "bad": "✗"}[verdict]
            ax.add_patch(Circle((x + 1.1, y - 3.1), 1.05, fc=color, ec="none", zorder=3))
            ax.text(x + 1.1, y - 3.15, glyph, fontsize=6.5, fontweight="bold", color=SURFACE, ha="center", va="center", zorder=4,
                    family="DejaVu Sans")
            ax.text(x + 3.2, y - 3.1, fmt(value), fontsize=NUMBER, fontweight="bold", color=INK, va="center")
            bar_x, bar_y, bar_w = x + 11.2, y - 3.6, col_w - 12
            ax.add_patch(Rectangle((bar_x, bar_y), bar_w, 1.0, fc=TRACK, ec="none", zorder=2))
            if value:
                ax.add_patch(Rectangle((bar_x, bar_y), bar_w * min(value / scale, 1), 1.0, fc=color, ec="none", zorder=3))
            if example:
                ax.text(x, y - 6.6, example, fontsize=NOTE, color=INK2, va="center")
    ax.plot([3, 97], [top - 4 * row_h, top - 4 * row_h], color=LINE, lw=0.6)

    x = 3.5
    for verdict, glyph, label in (("good", "✓", "达标"), ("warn", "!", "有隐患"), ("bad", "✗", "不达标")):
        color = {"good": GOOD, "warn": WARN, "bad": BAD}[verdict]
        ax.add_patch(Circle((x, 3.4), 0.75, fc=color, ec="none"))
        ax.text(x, 3.35, glyph, fontsize=5, fontweight="bold", color=SURFACE, ha="center", va="center", family="DejaVu Sans")
        ax.text(x + 1.2, 3.4, label, fontsize=NOTE, color=INK2, va="center")
        x += 7.5
    ax.text(x, 3.4, "“攻击”共 37 种手法：直接注入、埋在银行数据和转发短信里的指令、催促跳过验证等。",
            fontsize=NOTE, color=MUTED, va="center")
    ax.text(3, 1.3, "埋在数据里的指令这一代模型本身已能识破，三组都没有被骗；TRUSTA 的不同在于：即使模型被骗，系统也不放行。  "
                    "方法与逐条记录：github.com/piggyz666/Trusta 中 docs/compare",
            fontsize=NOTE - 0.6, color=MUTED, va="center")
    for ext in ("png", "svg", "pdf"):
        fig.savefig(OUT / f"figure-card.{ext}", facecolor=SURFACE)


if __name__ == "__main__":
    main()
