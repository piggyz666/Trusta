"""YELLOW queries (赛题权限表：订阅查询为黄色——执行前展示操作详情并获得明确确认).

Subscriptions and direct debits are only shown after the customer confirms the query on a card (in a conversation) or on
the 账单与订阅 page. A confirmation covers the same customer for CONSENT_SECONDS, so a page visit right after a
confirmed chat query does not ask twice. Every confirmation is written to the audit chain.
"""
import time

SCOPES = {"subscriptions": "订阅与代扣"}
CONSENT_SECONDS = 600
# chat tools whose results show subscriptions / direct debits
QUERY_TOOLS = {"subscriptions": {"list_subscriptions", "list_direct_debits", "review_subscriptions", "renewal_reminders"}}
SCHEMA = "CREATE TABLE IF NOT EXISTS query_consents(owner TEXT NOT NULL, scope TEXT NOT NULL, until REAL NOT NULL, PRIMARY KEY(owner, scope))"


class QueryConsent:
    def __init__(self, service):
        self.s = service
        with service.db.transaction() as conn:
            conn.execute(SCHEMA)

    def active(self, owner, scope):
        row = self.s.db.one("SELECT until FROM query_consents WHERE owner=? AND scope=?", (owner, scope))
        return bool(row and row["until"] > time.time())

    def grant(self, owner, scope, source):
        if scope not in SCOPES:
            raise ValueError(scope)
        until = time.time() + CONSENT_SECONDS
        with self.s.db.transaction() as conn:
            conn.execute("INSERT INTO query_consents VALUES(?,?,?) ON CONFLICT(owner, scope) DO UPDATE SET until=excluded.until",
                         (owner, scope, until))
            self.s.audit.append_in(conn, None, None, "QUERY_CONFIRMED", "chat", {"owner": owner, "scope": scope, "source": source,
                                                                                 "valid_until": until})
        return until


def scope_of(tool):
    return next((scope for scope, tools in QUERY_TOOLS.items() if tool in tools), None)


def card(query_id, scope):
    return {"type": "query_confirm", "card_id": "c_q_" + query_id[-8:], "query_id": query_id, "scope": scope,
            "title": "确认查询" + SCOPES[scope], "risk_level": "YELLOW",
            "fields": [{"label": "查询内容", "value": SCOPES[scope]},
                       {"label": "将显示", "value": "商户、扣费金额、扣费周期与下次扣费日"},
                       {"label": "说明", "value": f"按权限规则，{SCOPES[scope]}查询需你确认后执行；只读取，不改动任何订阅。"
                                                 f"确认后 {CONSENT_SECONDS // 60} 分钟内再次查询不再重复确认"}]}
