"""生活服务：只给第三方官方入口的跳转链接，不在本行交易。

From the customer's own words we pick out what to search (city, dates, show keyword) and build a link that opens the
partner site already filtered. Link formats were checked by hand against the live sites (Oct 2026); anything we can't
fill in safely falls back to the site's home page. Nothing here comes from the model: it only picks the category.
"""
import re
from datetime import date, datetime, timedelta
from urllib.parse import quote

# city -> (flight city code on Ctrip, 12306 station telecode, Ctrip hotel city id)
CITIES = {
    "北京": ("BJS", "BJP", 1), "上海": ("SHA", "SHH", 2), "天津": ("TSN", "TJP", 3), "重庆": ("CKG", "CQW", 4),
    "广州": ("CAN", "GZQ", 32), "深圳": ("SZX", "SZQ", 30), "杭州": ("HGH", "HZH", 17), "南京": ("NKG", "NJH", 12),
    "武汉": ("WUH", "WHN", 477), "成都": ("CTU", "CDW", 28), "西安": ("SIA", "XAY", 10), "长沙": ("CSX", "CSQ", 206),
    "郑州": ("CGO", "ZZF", None), "沈阳": ("SHE", "SYT", None), "厦门": ("XMN", "XMS", 25), "青岛": ("TAO", "QDK", 7),
    "昆明": ("KMG", "KMM", 34), "石家庄": ("SJW", "SJP", None), "合肥": ("HFE", "HFH", None), "福州": ("FOC", "FZS", None),
    "济南": ("TNA", "JNK", None), "苏州": (None, "SZH", 14), "大连": ("DLC", "DLT", None), "哈尔滨": ("HRB", "HBB", None),
    "南昌": ("KHN", "NCG", None), "贵阳": ("KWE", "GIW", None), "南宁": ("NNG", "NNZ", None), "兰州": ("LHW", "LZJ", None),
    "太原": ("TYN", "TYV", None), "长春": ("CGQ", "CCT", None), "宁波": ("NGB", "NGH", None), "无锡": ("WUX", "WXH", None),
    "海口": ("HAK", "VUQ", None), "三亚": ("SYX", "SEQ", 43), "乌鲁木齐": ("URC", "WAR", None), "呼和浩特": ("HET", "HHC", None),
    "银川": ("INC", "YIJ", None), "西宁": ("XNN", "XNO", None), "拉萨": ("LXA", "LSO", None), "珠海": ("ZUH", "ZHQ", None),
    "桂林": ("KWL", "GLZ", None), "温州": ("WNZ", "RZH", None), "泉州": ("JJN", "QYS", None), "烟台": ("YNT", "YAK", None),
    "丽江": ("LJG", "LHM", None), "大理": ("DLU", "DKM", None), "张家界": ("DYG", "DIQ", None), "黄山": ("TXN", "HKH", None),
    "洛阳": ("LYA", "LYF", None), "徐州": ("XUZ", "XCH", None), "扬州": ("YTY", "YLH", None), "佛山": (None, "FSQ", None),
    "东莞": (None, "RTQ", None), "绍兴": (None, "SOH", None),
    # abroad / Hong Kong, Macao, Taiwan: flights only
    "香港": ("HKG", None, None), "澳门": ("MFM", None, None), "台北": ("TPE", None, None), "东京": ("TYO", None, None),
    "大阪": ("OSA", None, None), "首尔": ("SEL", None, None), "曼谷": ("BKK", None, None), "新加坡": ("SIN", None, None),
    "吉隆坡": ("KUL", None, None), "伦敦": ("LON", None, None), "巴黎": ("PAR", None, None), "纽约": ("NYC", None, None),
    "悉尼": ("SYD", None, None),
}
CITY_RE = re.compile("|".join(sorted(CITIES, key=len, reverse=True)))
WEEK = "一二三四五六日天"

CATEGORIES = {
    "FLIGHT": ("机票", re.compile(r"机票|航班|飞机|飞往|坐飞机|往返票")),
    "TRAIN": ("火车票", re.compile(r"火车|高铁|动车|车票|12306")),
    "SHOW": ("演出票", re.compile(r"演唱会|演出|音乐节|话剧|音乐剧|脱口秀|livehouse|LIVEHOUSE|Livehouse|相声|舞剧|演奏会|音乐会")),
    "HOTEL": ("酒店", re.compile(r"酒店|住宿|宾馆|民宿|订房")),
    "MOVIE": ("电影票", re.compile(r"电影票|看电影|影院|电影院")),
    "UTILITY": ("生活缴费", re.compile(r"电费|水费|燃气费|煤气费|话费|充话费|宽带费|缴费")),
    "PUBLIC": ("社保医保", re.compile(r"社保|医保|公积金")),
}
INTENT = re.compile(r"查|订|买|看|有没有|有什么|多少钱|预订|预定|抢|帮我|想去|想看|要去|怎么|缴|交|充值|充话费|办")
# banking requests that merely mention a utility ("关闭电费代扣") stay with the banking tools
BANKING = re.compile(r"代扣|订阅|自动扣|转账|转给|转\d|汇款|还款|心愿金|规则|就提醒|超过")

HOMES = {
    "FLIGHT": [("携程机票", "https://flights.ctrip.com/")],
    "TRAIN": [("中国铁路 12306", "https://www.12306.cn/")],
    "SHOW": [("大麦", "https://m.damai.cn/"), ("猫眼演出", "https://show.maoyan.com/")],
    "HOTEL": [("携程酒店", "https://hotels.ctrip.com/")],
    "MOVIE": [("猫眼电影", "https://www.maoyan.com/"), ("淘票票", "https://dianying.taobao.com/")],
    "UTILITY": [("网上国网（电费）", "https://www.95598.cn/"), ("中国移动", "https://www.10086.cn/"),
                ("中国联通", "https://www.10010.com/"), ("中国电信", "https://www.189.cn/")],
    "PUBLIC": [("国家社会保险公共服务平台", "https://si.12333.gov.cn/"), ("国家医保服务平台", "https://fuwu.nhsa.gov.cn/"),
               ("全国住房公积金小程序指引", "https://www.mohurd.gov.cn/")],
}
ALLOWED_HOSTS = {"flights.ctrip.com", "hotels.ctrip.com", "kyfw.12306.cn", "www.12306.cn", "m.damai.cn", "show.maoyan.com",
                 "www.maoyan.com", "dianying.taobao.com", "www.95598.cn", "www.10086.cn", "www.10010.com", "www.189.cn",
                 "si.12333.gov.cn", "fuwu.nhsa.gov.cn", "www.mohurd.gov.cn"}


def category(text):
    hits = [key for key, (_, pattern) in CATEGORIES.items() if pattern.search(text)]
    if not hits and re.search(rf"({CITY_RE.pattern})\s*飞\s*(往)?\s*({CITY_RE.pattern})|飞(往|去)\s*({CITY_RE.pattern})", text):
        return "FLIGHT"  # “从北京飞成都”
    return hits[0] if hits else None


def wants_life_service(text):
    return bool(category(text)) and not BANKING.search(text) and bool(INTENT.search(text) or CITY_RE.search(text))


def adds_detail(text):
    """A follow-up such as “从上海出发” or “下周五” that completes an earlier request."""
    return not category(text) and not BANKING.search(text) and bool(CITY_RE.search(text) or dates(text, date.today())[0])


def homes_card(kind):
    label = CATEGORIES[kind][0]
    card = {"type": "life", "category": kind, "title": label, "fields": [], "complete": False,
            "links": [{"label": n, "url": u, "prefilled": False} for n, u in HOMES[kind]],
            "note": "跳转第三方网站，价格和余票以对方页面为准；本行不参与交易。"}
    return card, f"{label}需要在对方平台查询和办理，下面是官方入口；告诉我城市和日期，我可以直接带上条件打开。"


def _next_weekday(today, weekday, weeks_ahead):
    start = today - timedelta(days=today.weekday()) + timedelta(weeks=weeks_ahead)
    day = start + timedelta(days=weekday)
    return day if day >= today else day + timedelta(weeks=1)


def dates(text, today):
    """All dates mentioned, in order. Understands 今天/明天/后天/大后天, (这|下|下下)周X, X月X日/号, X号, YYYY-MM-DD, M/D."""
    found = []
    for m in re.finditer(r"(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})[日号]?|(\d{1,2})月(\d{1,2})[日号]?|(?<![\d月])(\d{1,2})/(\d{1,2})(?!\d)"
                         r"|(大后天|后天|明天|今天)|(下下|下个?|这个?|本)?(?:周|星期|礼拜)([一二三四五六日天])|(?<![\d月])(\d{1,2})[号日](?!\d)", text):
        try:
            if m.group(1):
                d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            elif m.group(4):
                d = date(today.year, int(m.group(4)), int(m.group(5)))
                d = d if d >= today else date(today.year + 1, d.month, d.day)
            elif m.group(6):
                d = date(today.year, int(m.group(6)), int(m.group(7)))
                d = d if d >= today else date(today.year + 1, d.month, d.day)
            elif m.group(8):
                d = today + timedelta(days={"今天": 0, "明天": 1, "后天": 2, "大后天": 3}[m.group(8)])
            elif m.group(10):
                prefix = m.group(9) or ""
                weeks = 2 if prefix.startswith("下下") else 1 if prefix.startswith("下") else 0
                d = _next_weekday(today, min(WEEK.index(m.group(10)), 6), weeks)
            else:
                day = int(m.group(11))
                d = date(today.year, today.month, day)
                if d < today:
                    month = today.month % 12 + 1
                    d = date(today.year + (month == 1), month, day)
        except ValueError:
            continue
        found.append((m.start(), d))
    stays = re.search(r"(住|玩|待)\s*(\d{1,2}|[一两二三四五六七八九十])\s*(晚|天|夜)", text)
    nights = None
    if stays:
        n = stays.group(2)
        nights = int(n) if n.isdigit() else {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}[n]
    return [d for _, d in sorted(found)], nights


def cities(text):
    """Cities in the order they appear; 从A到B / A飞B / A去B / 到B 取方向。"""
    found = [(m.start(), m.group()) for m in CITY_RE.finditer(text)]
    names = []
    for _, name in found:
        if name not in names:
            names.append(name)
    origin = destination = None
    pair = re.search(rf"(?:从)?({CITY_RE.pattern})\s*(?:到|飞|去|至|→|->|-|回)\s*({CITY_RE.pattern})", text)
    if pair:
        origin, destination = pair.group(1), pair.group(2)
    elif names:
        to = re.search(rf"(?:到|去|飞|往|飞往)\s*({CITY_RE.pattern})", text)
        if to:
            destination = to.group(1)
            rest = [n for n in names if n != destination]
            origin = rest[0] if rest else None
        elif len(names) >= 2:
            origin, destination = names[0], names[1]
        else:
            destination = names[0]
    return origin, destination, names


FILLER = re.compile(r"帮我|给我|麻烦|请|我想|我要|想要|想|要|查一下|查查|查询|查|看看|看一下|看|搜一下|搜|买|订|抢|有没有|有什么|"
                    r"最近|近期|附近|一下|的|了|吗|呢|吧|门票|票|场|一张|两张|几张|在|去|到|和|跟|一起|周末|今年|明年|这个月|下个月|啊|呀")


def show_keyword(text):
    words = CATEGORIES["SHOW"][1].findall(text)
    kind = words[0] if words else "演出"
    rest = CITY_RE.sub(" ", text)
    rest = re.sub(r"\d{4}[-/.年]\d{1,2}[-/.月]\d{1,2}[日号]?|\d{1,2}月\d{1,2}[日号]?|\d{1,2}月份?|大后天|后天|明天|今天|(下下|下个?|这个?|本)?(周|星期|礼拜)[一二三四五六日天]|\d{1,2}[号日]",
                  " ", rest)
    rest = CATEGORIES["SHOW"][1].sub(" ", rest)
    rest = FILLER.sub(" ", rest)
    rest = re.sub(r"[，。,.!！?？\s]+", " ", rest).strip()
    artist = max(rest.split(" "), key=len) if rest else ""
    return (artist if 2 <= len(artist) <= 16 else ""), kind


def _d(day):
    return f"{day.month}月{day.day}日（周{WEEK[day.weekday()]}）"


def understand(text, now=None):
    """Returns a card dict, or None when the text is not a life-service request."""
    kind = category(text)
    if not kind:
        return None
    today = (now or datetime.now()).date()
    when, nights = dates(text, today)
    origin, destination, names = cities(text)
    label = CATEGORIES[kind][0]
    understood, links, missing = [], [], []

    if kind == "FLIGHT":
        go = when[0] if when else today + timedelta(days=1)
        back = when[1] if len(when) > 1 and when[1] > go else None
        if origin and destination and CITIES[origin][0] and CITIES[destination][0]:
            a, b = CITIES[origin][0].lower(), CITIES[destination][0].lower()
            if back:
                url = f"https://flights.ctrip.com/online/list/round-{a}-{b}?depdate={go:%Y-%m-%d}_{back:%Y-%m-%d}&cabin=y_s&adult=1"
            else:
                url = f"https://flights.ctrip.com/online/list/oneway-{a}-{b}?depdate={go:%Y-%m-%d}&cabin=y_s&adult=1"
            links.append({"label": f"携程：{origin} → {destination}", "url": url, "prefilled": True})
            understood += [("行程", f"{origin} → {destination}" + ("（往返）" if back else "（单程）")),
                           ("出发", _d(go) + ("" if when else "，未说日期，默认明天")), *([("返程", _d(back))] if back else [])]
        else:
            missing.append("出发和目的城市" if not destination else "出发城市" if not origin else "目的城市")
            if destination:
                understood.append(("目的地", destination))
    elif kind == "TRAIN":
        go = when[0] if when else today + timedelta(days=1)
        if origin and destination and CITIES[origin][1] and CITIES[destination][1]:
            fs, ts = f"{origin},{CITIES[origin][1]}", f"{destination},{CITIES[destination][1]}"
            url = (f"https://kyfw.12306.cn/otn/leftTicket/init?linktypeid=dc&fs={quote(fs)}&ts={quote(ts)}"
                   f"&date={go:%Y-%m-%d}&flag=N,N,Y")
            links.append({"label": f"12306：{origin} → {destination}", "url": url, "prefilled": True})
            understood += [("行程", f"{origin} → {destination}"), ("日期", _d(go) + ("" if when else "，未说日期，默认明天"))]
        else:
            missing.append("出发和目的城市" if not destination else "出发城市" if not origin else "目的城市")
    elif kind == "HOTEL":
        city = destination or (names[0] if names else None)
        checkin = when[0] if when else today
        checkout = when[1] if len(when) > 1 and when[1] > checkin else checkin + timedelta(days=nights or 1)
        if city and CITIES[city][2]:
            url = (f"https://hotels.ctrip.com/hotels/list?city={CITIES[city][2]}"
                   f"&checkin={checkin:%Y/%m/%d}&checkout={checkout:%Y/%m/%d}")
            links.append({"label": f"携程酒店：{city}", "url": url, "prefilled": True})
            understood += [("城市", city), ("入住", _d(checkin)), ("离店", _d(checkout))]
        else:
            missing.append("城市" if not city else f"{city}（暂不支持直接带条件打开）")
    elif kind == "SHOW":
        artist, word = show_keyword(text)
        city = destination or (names[0] if names else None)
        keyword = " ".join(x for x in (artist, city if not artist else None, word if not artist else None) if x)
        links.append({"label": f"大麦搜索：{keyword}", "url": "https://m.damai.cn/shows/search.html?keyword=" + quote(keyword),
                      "prefilled": True})
        understood += [*([("演出者 / 关键词", artist)] if artist else []), ("类型", word), *([("城市", city)] if city else []),
                       *([("日期", _d(when[0]))] if when else [])]
        if when:
            missing.append("日期需要在大麦页面上再筛选")
    for name, url in HOMES[kind]:
        if not any(l["url"].split("/")[2] == url.split("/")[2] for l in links):
            links.append({"label": name, "url": url, "prefilled": False})
    links = [l for l in links if l["url"].split("/")[2] in ALLOWED_HOSTS]
    summary = "、".join(f"{k} {v}" for k, v in understood)
    if links and links[0]["prefilled"]:
        text_out = f"已按你的条件准备好{label}查询链接（{summary}）。点击后跳转到对方网站查询和购买，本行不参与交易，也不会代你付款。"
    else:
        need = "、".join(missing) if missing else ""
        text_out = (f"这类{label}需要在官方平台办理，下面是入口。" if kind in {"UTILITY", "PUBLIC", "MOVIE"}
                    else f"还缺少{need}，先给你官方入口；告诉我{need}，我可以直接带上条件打开。")
    card = {"type": "life", "category": kind, "title": label, "fields": [{"label": k, "value": v} for k, v in understood],
            "links": links, "complete": bool(links and links[0]["prefilled"]) or kind in {"UTILITY", "PUBLIC", "MOVIE"}, "note": "跳转第三方网站，价格和余票以对方页面为准；本行不参与交易。"}
    return card, text_out
