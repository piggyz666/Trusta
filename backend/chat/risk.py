"""Investor risk questionnaire (deterministic scoring). The model never scores or chooses answers."""
import hashlib
import json
import time

QUESTIONNAIRE_ID = "risk-v1"
QUESTIONS = [
    ("q1", "您的年龄段？", [("A", "18–30 岁", 4), ("B", "31–45 岁", 3), ("C", "46–60 岁", 2), ("D", "60 岁以上", 1)]),
    ("q2", "您有多少年投资理财经验？", [("A", "没有", 1), ("B", "1–3 年", 2), ("C", "3–5 年", 3), ("D", "5 年以上", 4)]),
    ("q3", "您能接受的最大本金亏损？", [("A", "不能亏损", 1), ("B", "5% 以内", 2), ("C", "5%–20%", 3), ("D", "20% 以上", 4)]),
    ("q4", "这笔钱多久内可能要用？", [("A", "随时可能用", 1), ("B", "1 年内", 2), ("C", "1–3 年", 3), ("D", "3 年以上", 4)]),
    ("q5", "您的主要投资目标？", [("A", "保本为主", 1), ("B", "稳定增值", 2), ("C", "较高收益", 3), ("D", "追求最高收益", 4)]),
]
LEVELS = {1: "保守型", 2: "稳健型", 3: "平衡型", 4: "成长型", 5: "进取型"}


def card():
    return {"type": "questionnaire", "questionnaire_id": QUESTIONNAIRE_ID, "title": "理财风险测评（5 题）",
            "questions": [{"id": qid, "text": text, "options": [{"value": v, "label": label} for v, label, _ in options]}
                          for qid, text, options in QUESTIONS],
            "submit": "POST /api/chat/sessions/{session_id}/risk-assessment"}


def score(answers):
    """answers: {"q1": "A", ...} -> (risk_level 1..5, answers_hash). Raises ValueError on incomplete / unknown answers."""
    if not isinstance(answers, dict) or set(answers) != {qid for qid, _, _ in QUESTIONS}:
        raise ValueError("INCOMPLETE")
    total = 0
    for qid, _, options in QUESTIONS:
        points = {value: p for value, _, p in options}
        if answers[qid] not in points:
            raise ValueError("UNKNOWN_OPTION")
        total += points[answers[qid]]
    level = 1 if total <= 7 else 2 if total <= 10 else 3 if total <= 13 else 4 if total <= 16 else 5
    if answers["q3"] == "A":  # cannot accept any loss -> conservative regardless of other answers
        level = 1
    digest = hashlib.sha256(json.dumps({"id": QUESTIONNAIRE_ID, "answers": answers}, sort_keys=True).encode()).hexdigest()
    return level, digest


def parameters(level, answers_hash, now=None):
    now = float(time.time() if now is None else now)
    return {"risk_level": level, "assessed_at": now, "expires_at": now + 365 * 86400, "answers_hash": answers_hash}
