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
    # Fictional shelf of a demo bank. Names are invented (series "安睿") and do not refer to any real product.
    products = [
        ("sandbox-r1", "安睿·活钱管理", 1, 10000, 0, "CNY", 1, "现金管理类；不保证本金和收益"),
        ("wp-r1-7d", "安睿·七天周周开", 1, 10000, 7, "CNY", 1, "每7天开放一次申赎；不保证本金和收益"),
        ("sandbox-r2", "安睿·稳健30天持有", 2, 100000, 30, "CNY", 1, "持有满30天可赎回；存在净值波动"),
        ("wp-r2-90d", "安睿·稳盈90天持有", 2, 100000, 90, "CNY", 1, "持有满90天可赎回；存在净值波动"),
        ("wp-r2-1y", "安睿·纯债一年定开", 2, 1000000, 365, "CNY", 1, "封闭一年，到期开放；利率上行时净值可能回撤"),
        ("wp-r3-gold", "安睿·黄金积存", 3, 10000, 0, "CNY", 1, "随金价涨跌，可能亏损；无业绩比较基准"),
        ("wp-r3-plus", "安睿·固收+半年持有", 3, 1000000, 180, "CNY", 1, "含少量权益资产，净值波动高于纯债"),
        ("sandbox-r4", "安睿·权益精选180天", 4, 100000, 180, "CNY", 1, "主要投资股票类资产；可能出现较大亏损"),
        ("wp-r5-global", "安睿·全球科技(QDII)", 5, 100000, 0, "CNY", 1, "投资境外科技股，受汇率与海外市场双重影响"),
        ("sandbox-inactive", "安睿·已下架产品", 1, 10000, 7, "CNY", 0, "已下架"),
    ]
    details = [
        ("sandbox-r1", "现金管理", "近7日年化 1.45%–1.85%", "T+0 赎回，单日快赎上限 ¥10,000",
         "主要投资银行存款、同业存单和短期债券，适合存放随时要用的钱。"),
        ("wp-r1-7d", "现金管理", "业绩比较基准 1.6%–2.0%", "每7天开放一次，到期日可赎回，T+1 到账",
         "七天一个周期，比活期多一点收益，周期内不能取出。"),
        ("sandbox-r2", "固定收益", "业绩比较基准 2.0%–2.6%", "每笔持有满30天后可赎回，T+1 到账",
         "以高等级债券为主，适合一两个月内不用的钱。"),
        ("wp-r2-90d", "固定收益", "业绩比较基准 2.3%–3.0%", "每笔持有满90天后可赎回，T+1 到账",
         "以中短久期债券为主，期限更长，波动仍较小。"),
        ("wp-r2-1y", "固定收益", "业绩比较基准 2.6%–3.3%", "封闭期一年，到期开放赎回，T+2 到账",
         "纯债策略，不投资股票；一年内无法提前取出。"),
        ("wp-r3-gold", "黄金", "无业绩比较基准，跟随国际金价", "交易日随时买卖，按实时金价折算克数，T+0 到账",
         "按金额积存黄金，持有的是黄金克数，收益完全取决于金价涨跌。"),
        ("wp-r3-plus", "固收+", "业绩比较基准 2.8%–4.2%", "每笔持有满180天后可赎回，T+1 到账",
         "约八成债券打底，搭配不超过两成的股票或可转债，追求略高于纯债的收益。"),
        ("sandbox-r4", "权益", "无业绩比较基准", "每笔持有满180天后可赎回，T+2 到账",
         "主要投资A股优质公司，长期持有的波动和回撤都可能很大。"),
        ("wp-r5-global", "QDII", "无业绩比较基准", "开放式，T+3 到账；遇境外节假日顺延",
         "通过QDII额度投资海外科技龙头，同时承担股价和人民币汇率波动。"),
        ("sandbox-inactive", "现金管理", "—", "已停止申购", "已下架，仅用于展示下架产品不会被推荐。"),
    ]
    db.execute("CREATE TABLE IF NOT EXISTS wealth_product_info(product_id TEXT PRIMARY KEY, category TEXT NOT NULL, "
               "benchmark TEXT NOT NULL, liquidity TEXT NOT NULL, description TEXT NOT NULL)")
    for row in products:
        # keep existing databases in step with the current shelf (ids are stable; wording may change)
        db.execute("UPDATE wealth_products SET name=?, risk_note=? WHERE id=?", (row[1], row[7], row[0]))
        db.execute("INSERT OR IGNORE INTO wealth_products VALUES(?,?,?,?,?,?,?,?)", row)
    for row in details:
        db.execute("INSERT OR REPLACE INTO wealth_product_info VALUES(?,?,?,?,?)", row)
