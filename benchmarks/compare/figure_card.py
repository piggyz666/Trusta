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
ICON = ROOT / "frontend-app" / "public" / "icon-512.png"

TITLE, NUMBER, BODY, NOTE = 13, 13, 7.6, 6.4
INK, INK2, MUTED, SURFACE = "#1A2230", "#4B5563", "#8A9099", "#FFFFFF"
NAVY, NAVY2, GOLD, TEAL = "#0B1A2C", "#9FB0C4", "#C9AE7C", "#0F7C78"
ROW_A, ROW_B, TRACK = "#F4F6F8", "#FFFFFF", "#E5E8EC"
GOOD, WARN, BAD = "#2E8B57", "#D98A1E", "#C8372D"  # status colours, always with a ✓ / ! / ✗ glyph
TINT = {"A": "#FCEDEA", "B": "#F3EEFB", "C": "#E8F3F2"}
ACCENT = {"A": "#E5704F", "B": "#9C7BD3", "C": TEAL}
W, H = 100.0, 69.5


def font():
    for name in ("Noto Sans CJK SC", "Noto Sans CJK JP"):
        if any(name == f.name for f in font_manager.fontManager.ttflist):
            return name
    return "DejaVu Sans"


def box(ax, x, y, w, h, r, **kw):
    """Rounded rectangle in data units (the axes use equal scale, so r is a true radius)."""
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle=f"round,pad=0,rounding_size={r}", **kw))


def mark(ax, x, y, verdict, r=1.15, size=7):
    color = {"good": GOOD, "warn": WARN, "bad": BAD}[verdict]
    ax.add_patch(Circle((x, y), r, fc=color, ec="none", zorder=4))
    ax.text(x, y - 0.05, {"good": "✓", "warn": "!", "bad": "✗"}[verdict], fontsize=size, fontweight="bold", color=SURFACE,
            ha="center", va="center", zorder=5, family="DejaVu Sans")


def main(lang="zh"):
    global TITLE, BODY, NOTE
    TITLE, BODY, NOTE = (13, 7.6, 6.4) if lang == "zh" else (11.6, 6.6, 5.7)  # Latin text sets wider in this face
    plt.rcParams.update({"font.family": "sans-serif", "font.sans-serif": [font(), "DejaVu Sans"], "svg.fonttype": "none",
                         "pdf.fonttype": 42, "axes.unicode_minus": False})
    report = json.loads((OUT / "report.json").read_text(encoding="utf-8"))
    s = report["summary"]
    pct = lambda v: (v or 0) * 100
    incidents = {k: s[k]["unconfirmed_writes"] + s[k]["prompt_leaks"] + s[k]["unsuitable_sales"] for k in "ABC"}
    zh = lang == "zh"
    t = TEXT[lang]
    unit = (lambda v: f"{v:.0f} 次") if zh else (lambda v: f"{v:.0f}")
    rows = [
        (t["q1"], t["h1"], 100, lambda v: f"{v:.1f}%" if v else "0%", {
            "A": (pct(s["A"]["attack_success"]), "bad", t["e1a"]),
            "B": (pct(s["B"]["attack_success"]), "good", t["e1b"]),
            "C": (pct(s["C"]["attack_success"]), "good", t["e1b"])}),
        (t["q2"], t["h2"], 20, unit, {
            "A": (incidents["A"], "bad", t["e2a"].format(w=s["A"]["unconfirmed_writes"], l=s["A"]["prompt_leaks"])),
            "B": (incidents["B"], "good", t["e2b"]),
            "C": (incidents["C"], "good", t["e2b"])}),
        (t["q3"], t["h3"], 100, lambda v: f"{v:.0f}%" if v in (0, 100) else f"{v:.1f}%", {
            "A": (pct(s["A"]["strong_auth_coverage"]), "bad", t["e3a"]),
            "B": (pct(s["B"]["strong_auth_coverage"]), "warn", t["e3b"]),
            "C": (pct(s["C"]["strong_auth_coverage"]), "good", t["e3c"])}),
        (t["q4"], t["h4"], 100, lambda v: f"{v:.0f}%", {
            "A": (pct(s["A"]["task_success_same_turn"]), "warn", t["e4a"]),
            "B": (pct(s["B"]["task_success_same_turn"]), "bad", t["e4b"]),
            "C": (pct(s["C"]["task_success_same_turn"]), "good", t["e4c"])}),
    ]
    arms = [("A", t["a"], t["a_note"]), ("B", t["b"], t["b_note"]), ("C", "TRUSTA", t["c_note"])]

    fig = plt.figure(figsize=(7.2, 7.2 * H / W), dpi=300, facecolor=SURFACE)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.set_aspect("equal")
    ax.set_axis_off()

    # header band
    ax.add_patch(Rectangle((0, H - 11), W, 11, fc=NAVY, ec="none"))
    ax.plot([3, 9], [H - 2.6, H - 2.6], color=GOLD, lw=1.8, solid_capstyle="round")
    ax.text(3, H - 5.0, t["title"], fontsize=TITLE, fontweight="bold",
            color=SURFACE, va="center")
    runs = sum(s[k]["runs"] for k in "ABC")
    ax.text(3, H - 8.6, t["sub"].format(model=report["meta"].get("model", ""), runs=runs), fontsize=NOTE + 0.3, color=NAVY2, va="center")

    col_x, col_w = [36.0, 57.5, 79.0], 19.0
    head_top, head_h = H - 13.2, 7.0
    for (arm, name, note), x in zip(arms, col_x):
        if arm == "C":
            box(ax, x - 1.2, head_top - head_h, col_w + 2.4, head_h, 1.0, fc=NAVY, ec="none", zorder=2)
            size = head_h - 1.4  # the icon, unaltered, inside the navy header
            ax.imshow(plt.imread(ICON), extent=(x - 0.2, x - 0.2 + size, head_top - head_h + 0.7, head_top - 0.7), zorder=3)
            ax.text(x + size + 0.8, head_top - 2.4, name, fontsize=BODY + 2.4, fontweight="bold", color=SURFACE, va="center")
            ax.text(x + size + 0.8, head_top - 5.0, note, fontsize=NOTE, color=GOLD, va="center")
        else:
            box(ax, x - 1.2, head_top - head_h, col_w + 2.4, head_h, 1.0, fc=TINT[arm], ec="none", zorder=2)
            ax.add_patch(Circle((x + 1.4, head_top - head_h / 2), 1.7, fc=ACCENT[arm], ec="none", zorder=3))
            ax.text(x + 1.4, head_top - head_h / 2 - 0.05, arm, fontsize=BODY, fontweight="bold", color=SURFACE, ha="center",
                    va="center", zorder=4)
            ax.text(x + 4.2, head_top - 2.4, name, fontsize=BODY + 1.2, fontweight="bold", color=INK, va="center")
            ax.text(x + 4.2, head_top - 5.0, note, fontsize=NOTE, color=INK2, va="center")

    top, row_h = head_top - head_h - 1.2, 8.8
    # TRUSTA column: tinted and outlined, through the score row
    body_h = 4 * row_h + 6.6
    box(ax, col_x[2] - 1.2, top - body_h, col_w + 2.4, body_h, 1.0, fc=TINT["C"], ec=TEAL, lw=0.9, zorder=1)
    for i, (question, how, scale, fmt, cells) in enumerate(rows):
        y = top - i * row_h
        if i % 2 == 0:
            box(ax, 2, y - row_h + 0.4, col_x[2] - 4.0, row_h - 0.4, 0.8, fc=ROW_A, ec="none", zorder=0.5)
        ax.add_patch(Circle((4.8, y - 3.2), 1.55, fc=NAVY, ec="none", zorder=3))
        ax.text(4.8, y - 3.25, str(i + 1), fontsize=BODY, fontweight="bold", color=GOLD, ha="center", va="center", zorder=4)
        ax.text(7.4, y - 3.2, question, fontsize=BODY + 0.4, fontweight="bold", color=INK, va="center")
        ax.text(7.4, y - 6.1, how, fontsize=NOTE, color=MUTED, va="center")
        for arm, x in zip("ABC", col_x):
            value, verdict, example = cells[arm]
            color = {"good": GOOD, "warn": WARN, "bad": BAD}[verdict]
            mark(ax, x + 1.2, y - 3.2, verdict)
            ax.text(x + 3.3, y - 3.2, fmt(value), fontsize=NUMBER, fontweight="bold", color=INK, va="center")
            bx, by, bw, bh = x + 11.4, y - 3.85, col_w - 11.6, 1.3
            box(ax, bx, by, bw, bh, bh / 2, fc=TRACK, ec="none", zorder=2)
            if value:
                box(ax, bx, by, max(bw * min(value / scale, 1), bh), bh, bh / 2, fc=color, ec="none", zorder=3)
            ax.text(x, y - 6.3, example, fontsize=NOTE, color=INK2 if verdict != "good" else "#2F6B4F", va="center")

    # overall score per column
    y = top - 4 * row_h - 3.2
    ax.text(7.4, y, t["total"], fontsize=BODY + 0.4, fontweight="bold", color=INK, va="center")
    for arm, x in zip("ABC", col_x):
        passed = sum(1 for *_, cells in rows if cells[arm][1] == "good")
        fc = GOOD if passed == 4 else (WARN if passed >= 2 else BAD)
        box(ax, x, y - 1.9, 13.5, 3.8, 1.9, fc=fc, ec="none", zorder=3)
        ax.text(x + 6.75, y - 0.05, t["passed"].format(n=passed), fontsize=BODY + 0.6, fontweight="bold", color=SURFACE, ha="center",
                va="center", zorder=4)

    # legend + notes
    x = 3.4
    for verdict, label in zip(("good", "warn", "bad"), t["legend"]):
        mark(ax, x, 4.0, verdict, r=0.8, size=5)
        ax.text(x + 1.3, 4.0, label, fontsize=NOTE, color=INK2, va="center")
        x += 7.4 if zh else 9.0
    ax.text(x, 4.0, t["note1"],
            fontsize=NOTE, color=MUTED, va="center")
    ax.text(3, 1.6, t["note2"], fontsize=NOTE - 0.4, color=MUTED, va="center")
    for ext in ("png", "svg", "pdf"):
        fig.savefig(OUT / (f"figure-card.{ext}" if zh else f"figure-card-en.{ext}"), facecolor=SURFACE)
    plt.close(fig)


TEXT = {
    "zh": {
        "title": "同一个大模型，三种搭法：只有 TRUSTA 既不出事，又能当场办成",
        "sub": "同一个模型（{model}）· 同一个银行 · 同一份 218 道题 · 每题每组 3 次 · 共 {runs:,} 次真实对话",
        "a": "不设防的智能体", "a_note": "模型直接调用银行接口", "b": "提示词加固", "b_note": "8 条安全规则 + 确认框", "c_note": "本作品",
        "q1": "会不会被话术骗着越权？", "h1": "攻击成功率 · 越低越好", "e1a": "称“已在柜台验证”就直接挂失", "e1b": "37 种攻击手法全部挡住",
        "q2": "真的出过事吗？", "h2": "越权执行 + 泄露提示词 + 不适当销售", "e2a": "{w} 次未确认就执行 · {l} 次念出提示词", "e2b": "一次也没有",
        "q3": "大额、改密等高风险操作，有强验证吗？", "h3": "强验证覆盖率 · 越高越好", "e3a": "转账、挂失、改密都不验证",
        "e3b": "靠模型自觉，有 2 次跳过了", "e3c": "由系统强制执行，不靠模型",
        "q4": "说一句话，能不能当场办成？", "h4": "正常业务当轮完成率 · 越高越好", "e4a": "办得快，但不设防", "e4b": "多数要先口头再问一遍",
        "e4c": "一句话办成，安全不打折",
        "total": "四项合计", "passed": "{n} / 4 项达标", "legend": ("达标", "有隐患", "不达标"),
        "note1": "攻击共 37 种手法：直接注入、埋在银行数据和转发短信里的指令、催促跳过验证、冒充员工与公检法等",
        "note2": "埋在数据里的指令这一代模型本身已能识破，三组都没被骗；TRUSTA 的不同在于即使模型被骗，系统也不放行。"
                 "  方法与逐条记录：github.com/piggyz666/Trusta · docs/compare",
    },
    "en": {
        "title": "One model, three ways to build the agent: only TRUSTA is safe and gets it done",
        "sub": "Same model ({model}) · same bank · same 218 test cases · 3 runs per case and approach · {runs:,} real conversations",
        "a": "Unguarded agent", "a_note": "Calls the bank API directly", "b": "Prompt-hardened", "b_note": "8 safety rules + confirm box",
        "c_note": "Our entry",
        "q1": "Can it be talked into overstepping?", "h1": "Attack success rate · lower is better",
        "e1a": "“I was verified” → card reported lost", "e1b": "All 37 attack techniques stopped",
        "q2": "Did anything actually go wrong?", "h2": "Unauthorised acts + leaks + mis-selling",
        "e2a": "{w} unconfirmed · {l} prompt leaks", "e2b": "Never",
        "q3": "High-risk operations strongly verified?", "h3": "Strong-verification coverage · higher is better",
        "e3a": "Transfers, lost card, PIN: none", "e3b": "Left to the model; skipped twice", "e3c": "Enforced by the system",
        "q4": "Done in the same turn?", "h4": "One-turn completion · higher is better",
        "e4a": "Fast, but unguarded", "e4b": "Usually asks again first", "e4c": "Done in one turn, safely",
        "total": "Overall", "passed": "{n} / 4 passed", "legend": ("Pass", "At risk", "Fail"),
        "note1": "37 attack techniques: direct injection, instructions in bank data and forwarded SMS, pressure to skip checks",
        "note2": "Models already see through instructions hidden in data; none of the three was fooled. TRUSTA's difference: even a fooled "
                 "model cannot get an operation through.  Records: github.com/piggyz666/Trusta",
    },
}


if __name__ == "__main__":
    main("zh")
    main("en")
