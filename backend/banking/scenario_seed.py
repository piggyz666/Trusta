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
    # Fictional shelf of a demo bank: its own deposits plus products it distributes for other (equally fictional)
    # issuers. No name here refers to a real company or product.
    # (id, name, risk, minimum cents, term days, issuer, category, group, benchmark, liquidity, description, risk note)
    shelf = [
        ("sandbox-r1", "活钱管理A", 1, 10000, 0, "青峦理财", "现金管理", "现金管理", "近7日年化 1.45%–1.85%", "T+0 赎回，单日快赎上限 ¥10,000",
         "主要投资银行存款、同业存单和短期债券，适合存放随时要用的钱。", "现金管理类；不保证本金和收益"),
        ("wp-r1-mmf", "云杉货币A", 1, 100, 0, "云杉基金", "货币基金", "现金管理", "七日年化约 1.3%（仅供参考）", "T+1 到账，可设快速赎回",
         "货币市场基金，1 元起投，适合零钱管理。", "货币基金不等于存款，不保证收益"),
        ("wp-r1-7d", "七天周周盈", 1, 10000, 7, "月湾理财", "现金管理", "现金管理", "业绩比较基准 1.6%–2.0%", "每7天开放一次，到期日可赎回，T+1 到账",
         "七天一个周期，比活期多一点收益，周期内不能取出。", "每7天开放一次申赎；不保证本金和收益"),
        ("wp-r1-dep1y", "一年期定期存款", 1, 5000, 365, "本行", "存款", "存款", "年利率 1.30%（存款，挂牌利率）", "到期自动转存；提前支取按活期计息",
         "银行存款，受存款保险保障，本金和利息确定。", "提前支取损失利息"),
        ("wp-r1-cd3y", "三年期大额存单", 1, 20000000, 1095, "本行", "存款", "存款", "年利率 1.75%（存款，挂牌利率）", "可转让；提前支取按活期计息",
         "20 万元起存，利率高于普通定期，受存款保险保障。", "期限长，提前支取损失利息"),
        ("sandbox-r2", "稳健30天持有", 2, 100000, 30, "青峦理财", "固定收益", "固收", "业绩比较基准 2.0%–2.6%", "每笔持有满30天后可赎回，T+1 到账",
         "以高等级债券为主，适合一两个月内不用的钱。", "持有期30天；存在净值波动"),
        ("wp-r2-90d", "稳盈90天持有", 2, 100000, 90, "砚池资管", "固定收益", "固收", "业绩比较基准 2.3%–3.0%", "每笔持有满90天后可赎回，T+1 到账",
         "以中短久期债券为主，期限更长，波动仍较小。", "持有期90天；存在净值波动"),
        ("wp-r2-shortbond", "云杉短债C", 2, 1000, 7, "云杉基金", "债券基金", "固收", "无业绩比较基准，近似短期债券收益", "持有满7天免赎回费，T+1 到账",
         "投资剩余期限较短的债券，波动小于一般债基。", "债券基金净值会波动，可能亏损"),
        ("wp-r2-ins180", "松溪稳健增值1号", 2, 100000, 180, "松溪保险资管", "固定收益", "固收", "业绩比较基准 2.5%–3.2%", "每笔持有满180天后可赎回，T+2 到账",
         "保险资管产品，以债券和债权计划为主。", "持有期180天；存在净值波动"),
        ("wp-r2-1y", "鹿鸣纯债一年定开", 2, 1000000, 365, "鹿鸣基金", "债券基金", "固收", "业绩比较基准 2.6%–3.3%", "封闭期一年，到期开放赎回，T+2 到账",
         "纯债策略，不投资股票；一年内无法提前取出。", "封闭一年，到期开放；利率上行时净值可能回撤"),
        ("wp-r3-gold", "黄金积存", 3, 10000, 0, "本行", "黄金", "黄金", "无业绩比较基准，跟随国际金价", "交易日随时买卖，按实时金价折算克数，T+0 到账",
         "按金额积存黄金，持有的是黄金克数，收益完全取决于金价涨跌。", "随金价涨跌，可能亏损；无业绩比较基准"),
        ("wp-r3-goldetf", "云杉黄金ETF联接A", 3, 1000, 0, "云杉基金", "黄金基金", "黄金", "跟踪上海金现货价格", "T+1 到账",
         "通过黄金ETF间接持有黄金，10 元起投。", "跟随金价涨跌，可能亏损"),
        ("wp-r3-plus", "月湾固收+半年持有", 3, 1000000, 180, "月湾理财", "固收+", "固收+", "业绩比较基准 2.8%–4.2%", "每笔持有满180天后可赎回，T+1 到账",
         "约八成债券打底，搭配不超过两成的股票或可转债，追求略高于纯债的收益。", "含少量权益资产，净值波动高于纯债"),
        ("wp-r3-balanced", "鹿鸣平衡配置", 3, 1000, 0, "鹿鸣基金", "混合基金", "固收+", "60% 中债综合 + 40% 沪深300", "开放式，T+2 到账",
         "股债大致四六开，定期再平衡，适合愿意承担中等波动的人。", "股票仓位约四成，可能出现阶段性亏损"),
        ("sandbox-r4", "权益精选180天", 4, 100000, 180, "砚池资管", "权益", "权益", "无业绩比较基准", "每笔持有满180天后可赎回，T+2 到账",
         "主要投资A股优质公司，长期持有的波动和回撤都可能很大。", "主要投资股票类资产；可能出现较大亏损"),
        ("wp-r4-csi300", "云杉沪深300指数A", 4, 1000, 0, "云杉基金", "指数基金", "权益", "跟踪沪深300指数", "开放式，T+1 到账；持有不足7天收取惩罚性赎回费",
         "被动跟踪大盘蓝筹，费率低，适合定投。", "随A股大盘涨跌，可能出现较大亏损"),
        ("wp-r4-dividend", "鹿鸣红利低波", 4, 1000, 0, "鹿鸣基金", "指数基金", "权益", "跟踪红利低波动指数", "开放式，T+1 到账",
         "选择分红稳定、波动较低的公司，股票基金里相对稳健的一类。", "仍是股票基金，可能出现较大亏损"),
        ("wp-r5-global", "鹿鸣全球科技(QDII)", 5, 100000, 0, "鹿鸣基金", "QDII基金", "QDII", "无业绩比较基准", "开放式，T+3 到账；遇境外节假日顺延",
         "通过QDII额度投资海外科技龙头，同时承担股价和人民币汇率波动。", "投资境外科技股，受汇率与海外市场双重影响"),
        ("wp-r5-ndx", "云杉纳斯达克100(QDII)", 5, 1000, 0, "云杉基金", "QDII基金", "QDII", "跟踪纳斯达克100指数", "开放式，T+3 到账；额度不足时暂停申购",
         "被动跟踪美国科技股指数，以人民币申购。", "受美股和汇率双重影响，波动很大"),
        ("sandbox-inactive", "已下架产品", 1, 10000, 7, "青峦理财", "现金管理", "现金管理", "—", "已停止申购",
         "已下架，仅用于展示下架产品不会被推荐。", "已下架"),
    ]
    products = [(row[0], row[1], row[2], row[3], row[4], "CNY", 0 if row[0] == "sandbox-inactive" else 1, row[11]) for row in shelf]
    details = [(row[0], row[6], row[8], row[9], row[10], row[5], row[7]) for row in shelf]
    db.execute("DROP TABLE IF EXISTS wealth_product_info")  # derived catalogue text, rebuilt on every start
    db.execute("CREATE TABLE wealth_product_info(product_id TEXT PRIMARY KEY, category TEXT NOT NULL, benchmark TEXT NOT NULL, "
               "liquidity TEXT NOT NULL, description TEXT NOT NULL, issuer TEXT NOT NULL, product_group TEXT NOT NULL)")
    for row in products:
        # keep existing databases in step with the current shelf (ids are stable; wording may change)
        db.execute("UPDATE wealth_products SET name=?, risk_note=? WHERE id=?", (row[1], row[7], row[0]))
        db.execute("INSERT OR IGNORE INTO wealth_products VALUES(?,?,?,?,?,?,?,?)", row)
    for row in details:
        db.execute("INSERT OR REPLACE INTO wealth_product_info VALUES(?,?,?,?,?,?,?)", row)
