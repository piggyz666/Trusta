"""Reference market quotes for the wealth page: gold and four currencies against CNY.

Read-only context, never an input to any bank decision. Every number carries its source and the time the source
published it. Sources are tried in order; if none answers, the last good value is served and marked stale, and if
there has never been one, a bundled snapshot is served and clearly labelled as such (so an offline demo still shows
something honest instead of a blank panel).
"""
import json
import threading
import time
import urllib.request
from datetime import datetime, timezone

OZ = 31.1034768  # grams per troy ounce
FX = ("USD", "EUR", "JPY", "HKD")
NAMES = {"XAU": "黄金", "USD": "美元", "EUR": "欧元", "JPY": "日元", "HKD": "港币"}
UNITS = {"XAU": "元/克", "USD": "元/1美元", "EUR": "元/1欧元", "JPY": "元/100日元", "HKD": "元/1港币"}
PER = {"JPY": 100}
TTL = 60            # seconds a fresh quote is reused
MIN_FORCED = 10     # a manual refresh cannot hit the sources more often than this
# Last resort when the machine has never been online. Values are approximate and dated; the page says so.
SNAPSHOT = {"as_of": "2026-10-23T08:00:00Z", "XAU_USD_OZ": 4110.0, "USD": 7.12, "EUR": 8.27, "JPY": 0.0470, "HKD": 0.916}


def _get(url, timeout=6, headers=None):
    request = urllib.request.Request(url, headers={"User-Agent": "TRUSTA/1.0", **(headers or {})})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat().replace("+00:00", "Z")


# ---- sources: each returns {"value": float, "as_of": iso, "source": str} or raises ----------------------------------
def gold_goldapi():
    data = json.loads(_get("https://api.gold-api.com/price/XAU"))
    price = float(data["price"])
    if not 300 < price < 30000:
        raise ValueError("implausible gold price")
    return {"value": price, "as_of": data.get("updatedAt") or _iso(time.time()), "source": "gold-api.com（国际现货金）"}


def gold_sina():
    raw = _get("https://hq.sinajs.cn/list=hf_XAU", headers={"Referer": "https://finance.sina.com.cn"}).decode("gbk", "replace")
    fields = raw.split('"')[1].split(",")
    price = float(fields[0])
    if not 300 < price < 30000:
        raise ValueError("implausible gold price")
    stamp = f"{fields[12]}T{fields[6]}+08:00" if len(fields) > 12 else _iso(time.time())
    return {"value": price, "as_of": stamp, "source": "新浪财经（伦敦金现货）"}


def fx_er_api():
    data = json.loads(_get("https://open.er-api.com/v6/latest/CNY"))
    if data.get("result") != "success":
        raise ValueError("er-api failed")
    rates = data["rates"]
    as_of = _iso(int(data["time_last_update_unix"]))
    return {code: {"value": 1 / float(rates[code]), "as_of": as_of, "source": "ExchangeRate-API（每日更新）"} for code in FX}


def fx_frankfurter():
    data = json.loads(_get("https://api.frankfurter.app/latest?from=CNY&to=" + ",".join(FX)))
    as_of = data["date"] + "T16:00:00+01:00"
    return {code: {"value": 1 / float(data["rates"][code]), "as_of": as_of, "source": "欧洲央行参考汇率（每日更新）"} for code in FX}


GOLD_SOURCES = (gold_goldapi, gold_sina)
FX_SOURCES = (fx_er_api, fx_frankfurter)


class MarketQuotes:
    def __init__(self, db=None, gold_sources=GOLD_SOURCES, fx_sources=FX_SOURCES):
        self.db, self.gold_sources, self.fx_sources = db, gold_sources, fx_sources
        self.lock = threading.Lock()
        self.cache, self.fetched_at, self.errors = {}, 0.0, []
        if db is not None:
            with db.transaction() as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS market_samples(symbol TEXT NOT NULL, at REAL NOT NULL, price REAL NOT NULL, "
                             "source TEXT NOT NULL, as_of TEXT NOT NULL, PRIMARY KEY(symbol, at))")
            self._restore()

    def _restore(self):
        for symbol in ("XAU_USD_OZ",) + FX:
            row = self.db.one("SELECT * FROM market_samples WHERE symbol=? ORDER BY at DESC LIMIT 1", (symbol,))
            if row:
                self.cache[symbol] = {"value": row["price"], "as_of": row["as_of"], "source": row["source"], "fetched_at": row["at"]}

    def _first(self, sources):
        errors = []
        for source in sources:
            try:
                return source(), errors
            except Exception as error:  # network, format or plausibility: try the next one
                errors.append(f"{source.__name__}: {type(error).__name__}")
        return None, errors

    def refresh(self, force=False, wait=True):
        if not self.lock.acquire(blocking=wait):
            return  # another refresh is already running
        try:
            now = time.time()
            if now - self.fetched_at < (MIN_FORCED if force else TTL):
                return
            self.fetched_at = now
            gold, gold_errors = self._first(self.gold_sources)
            fx, fx_errors = self._first(self.fx_sources)
            self.errors = gold_errors + fx_errors
            fresh = {}
            if gold:
                fresh["XAU_USD_OZ"] = gold
            if fx:
                fresh.update(fx)
            for symbol, quote in fresh.items():
                self.cache[symbol] = quote | {"fetched_at": now}
            if self.db is not None and fresh:
                with self.db.transaction() as conn:
                    for symbol, quote in fresh.items():
                        conn.execute("INSERT OR IGNORE INTO market_samples VALUES(?,?,?,?,?)",
                                     (symbol, now, quote["value"], quote["source"], quote["as_of"]))
                    conn.execute("DELETE FROM market_samples WHERE at < ?", (now - 7 * 86400,))
        finally:
            self.lock.release()

    def _quote(self, symbol, now):
        live = self.cache.get(symbol)
        if live:
            return live | {"stale": now - live["fetched_at"] > 15 * 60, "snapshot": False}
        return {"value": SNAPSHOT[symbol], "as_of": SNAPSHOT["as_of"], "source": "内置快照（未联网）",
                "fetched_at": None, "stale": True, "snapshot": True}

    def _history(self, symbol, since):
        if self.db is None:
            return []
        return [(r["at"], r["price"]) for r in self.db.rows(
            "SELECT at, price FROM market_samples WHERE symbol=? AND at>=? ORDER BY at", (symbol, since))]

    def view(self, force=False):
        if force or not self.cache:
            self.refresh(force)
        elif time.time() - self.fetched_at >= TTL:  # serve what we have now, update in the background
            threading.Thread(target=self.refresh, kwargs={"wait": False}, daemon=True).start()
        now = time.time()
        usd = self._quote("USD", now)
        gold = self._quote("XAU_USD_OZ", now)
        quotes = []
        gram = gold["value"] * usd["value"] / OZ
        usd_hist = dict(self._history("USD", now - 86400))
        history = [(at, price * usd_hist.get(at, usd["value"]) / OZ) for at, price in self._history("XAU_USD_OZ", now - 86400)]
        quotes.append({"symbol": "XAU", "name": NAMES["XAU"], "unit": UNITS["XAU"], "price": round(gram, 2),
                       "detail": f"国际现货 {gold['value']:,.2f} 美元/盎司，按美元汇率折算", "source": gold["source"],
                       "as_of": gold["as_of"], "stale": gold["stale"] or usd["stale"], "snapshot": gold["snapshot"] or usd["snapshot"],
                       "history": [[round(at), round(p, 2)] for at, p in history]})
        for code in FX:
            q = self._quote(code, now)
            per = PER.get(code, 1)
            quotes.append({"symbol": code + "CNY", "name": NAMES[code], "unit": UNITS[code], "price": round(q["value"] * per, 4),
                           "detail": None, "source": q["source"], "as_of": q["as_of"], "stale": q["stale"], "snapshot": q["snapshot"],
                           "history": [[round(at), round(p * per, 4)] for at, p in self._history(code, now - 86400)]})
        live = not any(q["snapshot"] for q in quotes)
        return {"quotes": quotes, "live": live, "fetched_at": self.fetched_at or None, "errors": self.errors,
                "note": "行情仅供参考，来自公开数据源，可能有延迟；不构成投资建议，也不是本行买卖报价。"}
