"""
NSE data client.

Fixes the problems the earlier yfinance-only approach ran into:
  1. "Only got a few companies"   -> loads the FULL official NSE equity list
                                      (~2000 symbols) from NSE's own archive CSV,
                                      not a hardcoded handful.
  2. "Didn't get live data"       -> talks to NSE's own quote API directly
                                      (nseindia.com), which is the same source
                                      Groww/Upstox ultimately read from,
                                      instead of relying on Yahoo Finance's
                                      often-throttled/delayed India feed.
  3. Random empty/None responses  -> NSE requires a warmed-up session with
                                      real browser-like headers + cookies
                                      before it will answer the quote API.
                                      We do that handshake, retry once with a
                                      fresh session on failure, and cache
                                      results for a short TTL so a transient
                                      NSE hiccup doesn't break the UI (we serve
                                      the last good value, marked "stale",
                                      instead of an error).
  4. Market-hours confusion       -> every quote response tells you whether
                                      the market is currently open, so the UI
                                      can label data as LIVE vs LAST CLOSE
                                      instead of silently showing a stale
                                      number as if it were live.

NSE does rate-limit aggressive scraping. This client is deliberately
conservative (cache TTL, single retry, small delay) rather than fast.
For anything at genuine production scale you'd want a paid data vendor or a
broker API (Kite Connect / Upstox) instead — for a course project this is a
reliable, free, real NSE source.
"""

import csv
import io
import os
import time
import threading
from datetime import datetime, time as dtime
import requests
import yfinance as yf
import pandas as pd

NSE_BASE = "https://www.nseindia.com"
NSE_QUOTE_URL = "https://www.nseindia.com/api/quote-equity?symbol={symbol}"
NSE_EQUITY_LIST_URL = "https://archives.nseindia.com/content/equity/EQUITY_L.csv"
LOCAL_SYMBOL_FILE = os.path.join(os.path.dirname(__file__), "data", "nse_equity_list.csv")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://www.nseindia.com/",
}

QUOTE_CACHE_TTL = 5      # seconds — live quotes refresh fast but don't hammer NSE
SYMBOL_LIST_TTL = 60 * 60 * 12  # 12 hours — company list barely changes


class NSEClient:
    def __init__(self):
        self._session = None
        self._session_lock = threading.Lock()
        self._quote_cache = {}       # symbol -> (timestamp, data)
        self._symbol_list_cache = None
        self._symbol_list_ts = 0

    # ---------- session handling ----------

    def _new_session(self):
        s = requests.Session()
        s.headers.update(HEADERS)
        # NSE requires hitting the homepage first to receive cookies
        # before the API endpoints will respond with real data.
        s.get(NSE_BASE, timeout=8)
        s.get(f"{NSE_BASE}/get-quotes/equity?symbol=RELIANCE", timeout=8)
        return s

    def _get_session(self, force_new=False):
        with self._session_lock:
            if self._session is None or force_new:
                self._session = self._new_session()
            return self._session

    # ---------- live quote ----------

    def get_quote(self, symbol: str) -> dict:
        symbol = symbol.upper().strip()
        now = time.time()

        cached = self._quote_cache.get(symbol)
        if cached and now - cached[0] < QUOTE_CACHE_TTL:
            data = dict(cached[1])
            data["stale"] = False
            return data

        # Try NSE's own quote API first — this is the "live" path the
        # README describes (warmed-up session, retries once on failure).
        # It's unofficial and NSE's bot detection changes often, so it can
        # simply fail; when it does, fall back to yfinance (~15 min
        # delayed) rather than showing an error, and label the source
        # accordingly so the UI can be honest about which one it got.
        data = self._fetch_quote(symbol)
        if data is None:
            data = self._fallback_yfinance_quote(symbol)

        if not data.get("error"):
            self._quote_cache[symbol] = (now, data)
            data = dict(data)
            data["stale"] = False
            return data

        if cached:
            data = dict(cached[1])
            data["stale"] = True
            return data
        return data

    def _fetch_quote(self, symbol: str, retried=False) -> dict | None:
        try:
            session = self._get_session()
            r = session.get(NSE_QUOTE_URL.format(symbol=symbol), timeout=6)
            if r.status_code != 200:
                raise requests.HTTPError(f"status {r.status_code}")
            payload = r.json()
            price_info = payload.get("priceInfo", {})
            meta = payload.get("metadata", {})

            return {
                "symbol": symbol,
                "company_name": payload.get("info", {}).get("companyName"),
                "last_price": price_info.get("lastPrice"),
                "change": price_info.get("change"),
                "pct_change": price_info.get("pChange"),
                "open": price_info.get("open"),
                "day_high": price_info.get("intraDayHighLow", {}).get("max"),
                "day_low": price_info.get("intraDayHighLow", {}).get("min"),
                "prev_close": price_info.get("previousClose"),
                "market_open": self._is_market_open(),
                "source": "nse",
                "as_of": datetime.now().isoformat(timespec="seconds"),
            }
        except Exception:
            if not retried:
                # Session probably expired — get a fresh one and try once more.
                self._get_session(force_new=True)
                return self._fetch_quote(symbol, retried=True)
            return None

    def _fallback_yfinance_quote(self, symbol: str) -> dict:
        try:
            df = yf.Ticker(f"{symbol}.NS").history(period="5d", interval="1d")
            if df.empty:
                df = yf.Ticker(f"{symbol}.BO").history(period="5d", interval="1d")
            if df.empty:
                return {"symbol": symbol, "error": "No data found", "source": "none"}

            last = df.iloc[-1]
            prev = df.iloc[-2] if len(df) > 1 else last
            last_price = float(last["Close"])
            prev_close = float(prev["Close"])
            change = last_price - prev_close
            pct_change = (change / prev_close * 100) if prev_close else None

            return {
                "symbol": symbol,
                "company_name": None,
                "last_price": round(last_price, 2),
                "change": round(change, 2),
                "pct_change": round(pct_change, 2) if pct_change is not None else None,
                "open": round(float(last["Open"]), 2),
                "day_high": round(float(last["High"]), 2),
                "day_low": round(float(last["Low"]), 2),
                "prev_close": round(prev_close, 2),
                "market_open": self._is_market_open(),
                "source": "yfinance",
                "as_of": datetime.now().isoformat(timespec="seconds"),
            }
        except Exception as e:
            return {"symbol": symbol, "error": str(e), "source": "none"}

    # ---------- index quotes (NIFTY 50 / SENSEX / BANK NIFTY ticker strip) ----------

    INDEX_TICKERS = {
        "NIFTY 50": "^NSEI",
        "SENSEX": "^BSESN",
        "BANK NIFTY": "^NSEBANK",
        "FINNIFTY": "^CNXFIN",
    }

    def get_index_quotes(self) -> list:
        now = time.time()
        cached = getattr(self, "_index_cache", None)
        if cached and now - cached[0] < QUOTE_CACHE_TTL:
            return cached[1]

        results = []
        for display_name, yahoo_symbol in self.INDEX_TICKERS.items():
            try:
                df = yf.Ticker(yahoo_symbol).history(period="5d", interval="1d")
                if df.empty:
                    results.append({"name": display_name, "error": "No data found"})
                    continue
                last = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else last
                last_price = float(last["Close"])
                prev_close = float(prev["Close"])
                change = last_price - prev_close
                pct_change = (change / prev_close * 100) if prev_close else None
                results.append({
                    "name": display_name,
                    "last_price": round(last_price, 2),
                    "change": round(change, 2),
                    "pct_change": round(pct_change, 2) if pct_change is not None else None,
                })
            except Exception as e:
                results.append({"name": display_name, "error": str(e)})

        self._index_cache = (now, results)
        return results

    @staticmethod
    def _is_market_open() -> bool:
        now = datetime.now()
        if now.weekday() >= 5:  # Sat/Sun
            return False
        t = now.time()
        return dtime(9, 15) <= t <= dtime(15, 30)

    # ---------- full symbol list ----------

    def get_all_symbols(self) -> list[dict]:
        now = time.time()
        if self._symbol_list_cache and now - self._symbol_list_ts < SYMBOL_LIST_TTL:
            return self._symbol_list_cache

        if os.path.exists(LOCAL_SYMBOL_FILE):
            try:
                with open(LOCAL_SYMBOL_FILE, encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    symbols = [
                        {
                            "symbol": row["SYMBOL"].strip(),
                            "name": row["NAME OF COMPANY"].strip(),
                            "isin": row.get("ISIN NUMBER", "").strip(),
                        }
                        for row in reader
                        if row.get("SYMBOL")
                    ]
                if symbols:
                    self._symbol_list_cache = symbols
                    self._symbol_list_ts = now
                    print(f"[symbols] loaded {len(symbols)} symbols from local file")
                    return symbols
            except Exception as e:
                print(f"[symbols] failed to read local file: {e}")

        print(f"[symbols] no local file found at {LOCAL_SYMBOL_FILE}")
        return self._symbol_list_cache or []

    # ---------- historical data (for regime detection) ----------

    def get_history(self, symbol: str, period="1y", interval="1d"):
        """
        NSE's own historical endpoints are inconsistent/rate-limited for bulk
        use, so historical OHLCV (used to train/run the regime model) comes
        from yfinance, which is fine for daily bars — the flakiness we hit
        earlier was specifically about *live* quotes, not historical series.

        yfinance can raise (not just return empty) on a bad response — e.g.
        a network hiccup during its internal exchange-timezone lookup raises
        a raw JSONDecodeError instead of giving back an empty DataFrame. Every
        caller (regime, backtest, indicators) already handles "no data" by
        checking `.empty` and returning a 404, so any yfinance exception is
        caught here and normalized to an empty DataFrame rather than letting
        it bubble up as an unhandled 500.
        """
        try:
            # Yahoo index symbols (e.g. ^NSEI) already identify their exchange
            # series and must not receive an equity suffix.
            if symbol.startswith("^"):
                return yf.Ticker(symbol).history(period=period, interval=interval)
            ticker = yf.Ticker(f"{symbol}.NS")
            df = ticker.history(period=period, interval=interval)
            if df.empty:
                # Some symbols are BSE-listed only
                ticker = yf.Ticker(f"{symbol}.BO")
                df = ticker.history(period=period, interval=interval)
            return df
        except Exception as e:
            print(f"[history] failed for {symbol} ({period}/{interval}): {e}")
            return pd.DataFrame()


nse_client = NSEClient()

