"""Fictional historical read data, independent of today's opening balance."""
from datetime import datetime
from backend.business import CHINA


def seed_scenarios(db):
    rows = [
        ("aug-food", "2026-08-03T12:00:00", "演示餐厅", "FOOD", 5000, "PURCHASE", "SETTLED"),
        ("aug-market", "2026-08-10T10:00:00", "演示超市", "GROCERIES", 10000, "PURCHASE", "SETTLED"),
        ("aug-trip", "2026-08-15T08:00:00", "演示地铁", "TRANSPORT", 1000, "PURCHASE", "SETTLED"),
        ("aug-shop", "2026-08-25T15:00:00", "演示商店", "SHOPPING", 14000, "PURCHASE", "SETTLED"),
        ("sep-coffee", "2026-09-01T08:30:00", "演示咖啡店", "FOOD", 3000, "PURCHASE", "SETTLED"),
        ("sep-lunch", "2026-09-03T12:00:00", "演示餐厅", "FOOD", 4500, "PURCHASE", "SETTLED"),
        ("sep-trip", "2026-09-05T08:00:00", "演示地铁", "TRANSPORT", 600, "PURCHASE", "SETTLED"),
        ("sep-market", "2026-09-10T10:00:00", "演示超市", "GROCERIES", 12000, "PURCHASE", "SETTLED"),
        ("sep-ride", "2026-09-12T20:00:00", "演示打车", "TRANSPORT", 2400, "PURCHASE", "SETTLED"),
        ("sep-shop", "2026-09-16T15:00:00", "演示网店", "SHOPPING", 18000, "PURCHASE", "SETTLED"),
        ("sep-large", "2026-09-20T14:00:00", "演示电子商店", "SHOPPING", 500000, "PURCHASE", "SETTLED"),
        ("sep-repeat-1", "2026-09-22T08:30:00", "演示咖啡店", "FOOD", 3000, "PURCHASE", "SETTLED"),
        ("sep-repeat-2", "2026-09-22T08:31:00", "演示咖啡店", "FOOD", 3000, "PURCHASE", "SETTLED"),
        ("sep-refund", "2026-09-24T15:00:00", "演示网店", "SHOPPING", 5000, "REFUND", "SETTLED"),
        ("sep-pending", "2026-09-25T15:00:00", "演示商店", "SHOPPING", 900000, "PURCHASE", "PENDING"),
        ("sep-end", "2026-09-30T23:59:59", "演示餐厅", "FOOD", 1000, "PURCHASE", "SETTLED"),
    ]
    for code, date, merchant, category, amount, kind, status in rows:
        db.execute("INSERT OR IGNORE INTO consumption_records VALUES(?,?,?,?,?,?,?,?,?)",
                   (code, "acct-demo", merchant, category, amount, "CNY", datetime.fromisoformat(date).replace(tzinfo=CHINA).timestamp(), kind, status))
    db.execute("INSERT OR IGNORE INTO risk_profiles VALUES('demo',2,?,?)",
               (datetime(2026, 1, 1, tzinfo=CHINA).timestamp(), datetime(2030, 1, 1, tzinfo=CHINA).timestamp()))
    products = [
        ("sandbox-r1", "演示低风险现金产品", 1, 10000, 0, "CNY", 1, "演示数据；不保证本金或收益"),
        ("sandbox-r2", "演示稳健30天产品", 2, 100000, 30, "CNY", 1, "持有期30天；存在净值波动"),
        ("sandbox-r4", "演示高风险180天产品", 4, 100000, 180, "CNY", 1, "高风险；存在较大净值波动"),
        ("sandbox-inactive", "演示下架产品", 1, 10000, 7, "CNY", 0, "已下架"),
    ]
    for row in products:
        db.execute("INSERT OR IGNORE INTO wealth_products VALUES(?,?,?,?,?,?,?,?)", row)
