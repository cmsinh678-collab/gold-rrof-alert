import os
import json
import time
import tempfile
import threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import requests

# ============================================================
# CONFIG
# ============================================================

MANUAL_SYMBOLS = ["XAU-USDT-SWAP", "ETH-USDT-SWAP"]

TIMEFRAME = "15m"
CANDLE_LIMIT = 200

# GitHub Actions chạy 1 lần rồi thoát.
# Không dùng while True ở đây.

# Top movers
TOP_GAINERS_COUNT = 50
TOP_LOSERS_COUNT = 50
MIN_VOLUME_24H_USD = 20_000_000
TOP_MOVERS_TIMEFRAME = "1H"
TOP_MOVERS_MAX_WORKERS = 20

# Stop Hunt — NGƯỠNG 10%
STOP_HUNT_SWEEP_PCT = 2.0       # 10% — sập/vọt từ open
STOP_HUNT_RECOVER_PCT = 2.0     # 10% — hồi phục từ đáy/đỉnh
STOP_HUNT_REQUIRE_DIRECTIONAL_CLOSE = True
STOP_HUNT_VOLUME_MULT = 0.0
STOP_HUNT_VOLUME_LOOKBACK = 20
STOP_HUNT_MULTIBAR_ENABLED = True
STOP_HUNT_MAX_LOOKBACK_BARS = 3
STOP_HUNT_MAX_WORKERS = 20

# RROF / Everex
RROF_LENGTH = 10
RROF_MA_TYPE = "WMA"
SMOOTH = 3
SIGNAL_LENGTH = 5
SIGNAL_MA_TYPE = "WMA"
LOOKBACK = 20
LOOKBACK_MA_TYPE = "SMA"

# API
OKX_BASE_URL = "https://www.okx.com"
OKX_CANDLES_URL = f"{OKX_BASE_URL}/api/v5/market/candles"
OKX_TICKERS_URL = f"{OKX_BASE_URL}/api/v5/market/tickers"
OKX_INSTRUMENTS_URL = f"{OKX_BASE_URL}/api/v5/public/instruments"

REQUEST_TIMEOUT = 12
TELEGRAM_TIMEOUT = 15

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

STATE_FILE = "signal_state.json"
STATE_LOCK = threading.Lock()

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "OKX-RROF-StopHunt-GitHubActions/1.0",
    "Accept": "application/json",
})


# ============================================================
# STATE
# ============================================================

def load_state():
    with STATE_LOCK:
        if not os.path.exists(STATE_FILE):
            return {}
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print(f"⚠️ Không đọc được state: {e}")
            return {}


def save_state(state):
    with STATE_LOCK:
        directory = os.path.dirname(os.path.abspath(STATE_FILE)) or "."
        fd, tmp = tempfile.mkstemp(prefix=".state_", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, STATE_FILE)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass


def is_signal_reported(symbol, signal_type, timestamp):
    state = load_state()
    return state.get(f"{symbol}_{signal_type}_{timestamp}", False)


def mark_signal_reported(symbol, signal_type, timestamp):
    state = load_state()
    state[f"{symbol}_{signal_type}_{timestamp}"] = True
    save_state(state)


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("⚠️ Thiếu TELEGRAM_BOT_TOKEN hoặc TELEGRAM_CHAT_ID")
        print(message)
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = SESSION.post(
            url,
            json={"chat_id": CHAT_ID, "text": message},
            timeout=TELEGRAM_TIMEOUT,
        )
        print(f"📨 Telegram HTTP: {r.status_code}")
        if r.status_code != 200:
            print(f"❌ Telegram: {r.text[:500]}")
            return False
        return True
    except requests.RequestException as e:
        print(f"❌ Telegram error: {e}")
        return False


# ============================================================
# OKX HELPERS
# ============================================================

def okx_get(url, params, timeout=REQUEST_TIMEOUT):
    try:
        r = SESSION.get(url, params=params, timeout=timeout)
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("code") != "0":
            return None
        return data
    except (requests.RequestException, ValueError):
        return None


def resolve_manual_symbols():
    """Resolve exact requested symbols. Không quét toàn bộ instruments nhiều lần."""
    result = {}

    data = okx_get(
        OKX_INSTRUMENTS_URL,
        {"instType": "SWAP"},
        timeout=REQUEST_TIMEOUT,
    )
    if not data:
        print("⚠️ Không lấy được danh sách SWAP từ OKX")
        return result

    available = {x.get("instId") for x in data.get("data", [])}

    for requested in MANUAL_SYMBOLS:
        if requested in available:
            result[requested] = requested
            print(f"✅ {requested}")
        else:
            print(f"❌ Không tìm thấy {requested}")

    return result


def get_okx_candles(symbol, bar=TIMEFRAME, limit=CANDLE_LIMIT, quiet=False):
    data = okx_get(
        OKX_CANDLES_URL,
        {
            "instId": symbol,
            "bar": bar,
            "limit": str(limit),
        },
    )

    if not data:
        if not quiet:
            print(f"❌ {symbol}: candle API failed")
        return None

    rows = []
    for c in data.get("data", []):
        if len(c) < 9:
            continue
        rows.append({
            "timestamp": c[0],
            "open": c[1],
            "high": c[2],
            "low": c[3],
            "close": c[4],
            "volume": c[5],
            "volume_currency": c[6],
            "volume_quote": c[7],
            "confirm": c[8],
        })

    if not rows:
        return None

    df = pd.DataFrame(rows)

    for col in [
        "open", "high", "low", "close",
        "volume", "volume_currency", "volume_quote"
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df["timestamp"] = pd.to_datetime(
        pd.to_numeric(df["timestamp"], errors="coerce"),
        unit="ms",
        utc=True,
    )

    df = (
        df.sort_values("timestamp")
          .drop_duplicates("timestamp")
          .dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])
          .reset_index(drop=True)
    )

    if len(df) < 2:
        return None

    return df


# ============================================================
# TOP MOVERS
# ============================================================

def get_swap_usdt_tickers():
    data = okx_get(OKX_TICKERS_URL, {"instType": "SWAP"})
    if not data:
        print("❌ Không lấy được OKX SWAP tickers")
        return []

    result = []
    for t in data.get("data", []):
        inst_id = t.get("instId", "")
        if not inst_id.endswith("-USDT-SWAP"):
            continue

        try:
            last = float(t.get("last") or 0)
            vol_ccy_24h = float(t.get("volCcy24h") or 0)
            vol_24h = float(t.get("vol24h") or 0)
        except (TypeError, ValueError):
            continue

        if last <= 0:
            continue

        # Với SWAP, volCcy24h có thể là số lượng base.
        # Dùng cả hai nguồn và lấy notional lớn hơn.
        usd_volume = max(vol_ccy_24h * last, vol_24h * last)

        if usd_volume < MIN_VOLUME_24H_USD:
            continue

        result.append({
            "instId": inst_id,
            "last": last,
            "usd_volume": usd_volume,
        })

    return result


def get_1h_change(item):
    inst_id = item["instId"]

    data = okx_get(
        OKX_CANDLES_URL,
        {
            "instId": inst_id,
            "bar": TOP_MOVERS_TIMEFRAME,
            "limit": "4",
        },
        timeout=REQUEST_TIMEOUT,
    )

    if not data:
        return None

    confirmed = []
    for c in data.get("data", []):
        if len(c) < 9:
            continue
        if str(c[8]) != "1":
            continue
        try:
            close = float(c[4])
        except (TypeError, ValueError):
            continue
        confirmed.append((c[0], close))

    # OKX trả mới -> cũ. Cần 2 nến đã đóng.
    if len(confirmed) < 2:
        return None

    last_close = confirmed[0][1]
    previous_close = confirmed[1][1]

    if previous_close <= 0:
        return None

    pct = (last_close - previous_close) / previous_close * 100

    return {
        "instId": inst_id,
        "pct_1h": pct,
        "usd_volume": item["usd_volume"],
    }


def get_top_movers():
    print("🔎 Lấy danh sách SWAP USDT...")
    candidates = get_swap_usdt_tickers()

    print(
        f"✅ Sau lọc volume >= ${MIN_VOLUME_24H_USD / 1e6:.1f}M: "
        f"{len(candidates)} coin"
    )

    if not candidates:
        return [], [], []

    results = []

    print(f"⚡ Tính % 1H song song ({TOP_MOVERS_MAX_WORKERS} workers)...")

    with ThreadPoolExecutor(max_workers=TOP_MOVERS_MAX_WORKERS) as executor:
        futures = {
            executor.submit(get_1h_change, item): item["instId"]
            for item in candidates
        }

        total = len(futures)
        for i, future in enumerate(as_completed(futures), 1):
            try:
                result = future.result()
                if result:
                    results.append(result)
            except Exception as e:
                symbol = futures[future]
                print(f"⚠️ {symbol}: {e}")

            if i % 50 == 0 or i == total:
                print(f"   Top movers: {i}/{total}")

    if not results:
        return [], [], []

    results.sort(key=lambda x: x["pct_1h"], reverse=True)

    gainers = results[:min(TOP_GAINERS_COUNT, len(results))]
    losers = list(reversed(results[-min(TOP_LOSERS_COUNT, len(results)):]))

    top_symbols = list(dict.fromkeys(
        [x["instId"] for x in gainers] +
        [x["instId"] for x in losers]
    ))

    print("\n🚀 TOP GAINERS:")
    for i, x in enumerate(gainers[:10], 1):
        print(f" {i:2d}. {x['instId']:<24} {x['pct_1h']:+.2f}%")

    print("\n💥 TOP LOSERS:")
    for i, x in enumerate(losers[:10], 1):
        print(f" {i:2d}. {x['instId']:<24} {x['pct_1h']:+.2f}%")

    print(f"\n📋 Tổng coin Stop Hunt: {len(top_symbols)}")

    return gainers, losers, top_symbols


# ============================================================
# MOVING AVERAGES / RROF
# ============================================================

def wma(series, length):
    weights = np.arange(1, length + 1, dtype=float)
    return series.rolling(length).apply(
        lambda x: np.dot(x, weights) / weights.sum(),
        raw=True,
    )


def get_average(series, length, ma_type):
    ma_type = ma_type.upper()

    if ma_type == "SMA":
        return series.rolling(length).mean()
    if ma_type == "EMA":
        return series.ewm(span=length, adjust=False).mean()
    if ma_type == "RMA":
        return series.ewm(alpha=1 / length, adjust=False).mean()
    if ma_type == "WMA":
        return wma(series, length)

    raise ValueError(f"Unsupported MA type: {ma_type}")


def normalize(value, average):
    average = average.replace(0, np.nan)
    x = value / average

    result = np.select(
        [
            x > 1.50,
            x > 1.20,
            x > 1.00,
            x > 0.80,
            x > 0.60,
            x > 0.40,
            x > 0.20,
        ],
        [1.00, 0.90, 0.80, 0.70, 0.60, 0.50, 0.25],
        default=0.10,
    )

    return pd.Series(result, index=value.index)


def calculate_everex(df):
    df = df.copy()

    o = df["open"]
    h = df["high"]
    l = df["low"]
    c = df["close"]
    v = df["volume"]

    vola = get_average(v, LOOKBACK, LOOKBACK_MA_TYPE)
    vola_n = normalize(v, vola) * 100

    bar_spread = c - o
    bar_range = (h - l).replace(0, np.nan)

    r2 = (h.rolling(2).max() - l.rolling(2).min()).replace(0, np.nan)

    src_shift = c.diff()
    sign_spread = np.sign(bar_spread)
    sign_shift = np.sign(src_shift)

    barclosing = (2 * (c - l) / bar_range * 100) - 100
    s2r = bar_spread / bar_range * 100

    bar_spread_abs = abs(bar_spread)
    bar_spread_avg = get_average(
        bar_spread_abs, LOOKBACK, LOOKBACK_MA_TYPE
    )
    bar_spread_ratio_n = (
        normalize(bar_spread_abs, bar_spread_avg)
        * 100
        * sign_spread
    )

    low2 = l.rolling(2).min()
    barclosing_2 = (2 * (c - low2) / r2 * 100) - 100
    shift2bar_to_r2 = src_shift / r2 * 100

    src_shift_abs = abs(src_shift)
    srcshift_avg = get_average(
        src_shift_abs, LOOKBACK, LOOKBACK_MA_TYPE
    )
    srcshift_ratio_n = (
        normalize(src_shift_abs, srcshift_avg)
        * 100
        * sign_shift
    )

    pricea_n = (
        barclosing
        + s2r
        + bar_spread_ratio_n
        + barclosing_2
        + shift2bar_to_r2
        + srcshift_ratio_n
    ) / 6

    bar_flow = pricea_n * vola_n / 100

    bulls = bar_flow.clip(lower=0)
    bears = (-bar_flow.clip(upper=0))

    bulls_avg = get_average(bulls, RROF_LENGTH, RROF_MA_TYPE)
    bears_avg = get_average(bears, RROF_LENGTH, RROF_MA_TYPE)
    bears_avg = bears_avg.replace(0, np.nan)

    dx = bulls_avg / bears_avg

    rrof = 2 * (100 - 100 / (1 + dx)) - 100
    rrof_s = get_average(rrof, SMOOTH, "WMA")
    signal = get_average(rrof_s, SIGNAL_LENGTH, SIGNAL_MA_TYPE)

    df["RROF"] = rrof
    df["RROF_S"] = rrof_s
    df["SIGNAL"] = signal

    return df


# ============================================================
# RROF SIGNAL
# ============================================================

def check_rrof_signal(df, symbol):
    result = {
        "symbol": symbol,
        "signal": None,
        "price": None,
        "rrof_s": None,
        "signal_line": None,
        "timestamp": None,
        "volume": None,
    }

    confirmed = df[df["confirm"].astype(str) == "1"].copy()

    if len(confirmed) < 2:
        print(f"⚠️ {symbol}: không đủ 2 nến đóng")
        return result

    td2 = confirmed.iloc[-2]
    td1 = confirmed.iloc[-1]

    if pd.isna(td2["RROF_S"]) or pd.isna(td2["SIGNAL"]):
        return result
    if pd.isna(td1["RROF_S"]) or pd.isna(td1["SIGNAL"]):
        return result

    print(
        f"📊 {symbol} | "
        f"TD2 RROF_S={td2['RROF_S']:.2f} "
        f"SIG={td2['SIGNAL']:.2f} | "
        f"TD1 RROF_S={td1['RROF_S']:.2f} "
        f"SIG={td1['SIGNAL']:.2f}"
    )

    result.update({
        "price": float(td1["close"]),
        "rrof_s": float(td1["RROF_S"]),
        "signal_line": float(td1["SIGNAL"]),
        "timestamp": td1["timestamp"],
        "volume": float(td1["volume"]),
    })

    ts_key = td1["timestamp"].strftime("%Y%m%d%H%M")

    if (
        td2["RROF_S"] <= td2["SIGNAL"]
        and td1["RROF_S"] > td1["SIGNAL"]
    ):
        result["signal"] = "LONG"
        result["state_key"] = ("LONG", ts_key)
        return result

    if (
        td2["RROF_S"] >= td2["SIGNAL"]
        and td1["RROF_S"] < td1["SIGNAL"]
    ):
        result["signal"] = "SHORT"
        result["state_key"] = ("SHORT", ts_key)
        return result

    return result


# ============================================================
# STOP HUNT
# ============================================================

def bull_rejection(o_swing, low_swing, close_now):
    if o_swing <= 0 or low_swing <= 0:
        return None

    sweep = (o_swing - low_swing) / o_swing * 100
    recover = (close_now - low_swing) / low_swing * 100

    if sweep < STOP_HUNT_SWEEP_PCT:
        return None
    if recover < STOP_HUNT_RECOVER_PCT:
        return None
    if STOP_HUNT_REQUIRE_DIRECTIONAL_CLOSE and close_now <= o_swing:
        return None

    return sweep, recover


def bear_rejection(o_swing, high_swing, close_now):
    if o_swing <= 0 or high_swing <= 0:
        return None

    sweep = (high_swing - o_swing) / o_swing * 100
    recover = (high_swing - close_now) / high_swing * 100

    if sweep < STOP_HUNT_SWEEP_PCT:
        return None
    if recover < STOP_HUNT_RECOVER_PCT:
        return None
    if STOP_HUNT_REQUIRE_DIRECTIONAL_CLOSE and close_now >= o_swing:
        return None

    return sweep, recover


def detect_stop_hunt(df, symbol):
    confirmed = df[df["confirm"].astype(str) == "1"].copy()

    if len(confirmed) < STOP_HUNT_VOLUME_LOOKBACK + 2:
        return None

    last_bar = confirmed.iloc[-1]
    volume = float(last_bar["volume"])

    if STOP_HUNT_VOLUME_MULT > 0:
        avg_vol = confirmed["volume"].iloc[
            -(STOP_HUNT_VOLUME_LOOKBACK + 1):-1
        ].mean()

        if pd.notna(avg_vol) and avg_vol > 0:
            if volume < avg_vol * STOP_HUNT_VOLUME_MULT:
                return None

    max_bars = (
        STOP_HUNT_MAX_LOOKBACK_BARS
        if STOP_HUNT_MULTIBAR_ENABLED
        else 1
    )
    max_bars = min(max_bars, len(confirmed))

    for bars in range(1, max_bars + 1):
        window = confirmed.iloc[-bars:]
        first = window.iloc[0]
        last = window.iloc[-1]

        o = float(first["open"])
        c = float(last["close"])
        low = float(window["low"].min())
        high = float(window["high"].max())
        ts = last["timestamp"]

        bull = bull_rejection(o, low, c)
        if bull:
            sweep, recover = bull
            return {
                "symbol": symbol,
                "signal": "BULL_STOP_HUNT",
                "side": "LONG",
                "price": c,
                "sweep_pct": sweep,
                "recover_pct": recover,
                "volume": volume,
                "timestamp": ts,
                "bars": bars,
            }

        bear = bear_rejection(o, high, c)
        if bear:
            sweep, recover = bear
            return {
                "symbol": symbol,
                "signal": "BEAR_STOP_HUNT",
                "side": "SHORT",
                "price": c,
                "sweep_pct": sweep,
                "recover_pct": recover,
                "volume": volume,
                "timestamp": ts,
                "bars": bars,
            }

    return None


# ============================================================
# SCANNERS
# ============================================================

def scan_manual_rrof():
    print("\n" + "=" * 70)
    print("PHẦN 1 — XAU/ETH RROF")
    print("=" * 70)

    results = []
    symbols = resolve_manual_symbols()

    for requested in MANUAL_SYMBOLS:
        symbol = symbols.get(requested)
        if not symbol:
            continue

        print(f"\n🔍 {symbol}")

        df = get_okx_candles(symbol, TIMEFRAME, CANDLE_LIMIT)
        if df is None:
            continue

        df = calculate_everex(df)
        result = check_rrof_signal(df, symbol)

        if result["signal"]:
            results.append(result)
            print(f"🚨 RROF {result['signal']} {symbol}")
        else:
            print(f"🚫 Không có RROF crossover: {symbol}")

    return results


def scan_one_stophunt(symbol):
    try:
        df = get_okx_candles(
            symbol,
            TIMEFRAME,
            CANDLE_LIMIT,
            quiet=True,
        )
        if df is None:
            return None

        return detect_stop_hunt(df, symbol)
    except Exception as e:
        print(f"⚠️ {symbol}: {e}")
        return None


def scan_top_movers_stophunt():
    print("\n" + "=" * 70)
    print("PHẦN 2 — TOP MOVERS STOP HUNT")
    print("=" * 70)

    _, _, symbols = get_top_movers()

    if not symbols:
        return []

    results = []

    with ThreadPoolExecutor(max_workers=STOP_HUNT_MAX_WORKERS) as executor:
        futures = {
            executor.submit(scan_one_stophunt, symbol): symbol
            for symbol in symbols
        }

        total = len(futures)

        for i, future in enumerate(as_completed(futures), 1):
            symbol = futures[future]

            try:
                result = future.result()
                if result:
                    results.append(result)
                    print(
                        f"🚨 {result['signal']} {symbol} "
                        f"sweep={result['sweep_pct']:.2f}% "
                        f"recover={result['recover_pct']:.2f}%"
                    )
            except Exception as e:
                print(f"⚠️ {symbol}: {e}")

            if i % 10 == 0 or i == total:
                print(f"   Stop Hunt: {i}/{total}")

    return results


# ============================================================
# MESSAGE + STATE
# ============================================================

def clean_symbol(symbol):
    return symbol.replace("-SWAP", "").replace("-", "")


def build_messages(rrof_results, stop_results):
    candidates = []

    for r in rrof_results:
        side, ts_key = r["state_key"]

        if not is_signal_reported(r["symbol"], side, ts_key):
            candidates.append({
                "type": "RROF",
                "result": r,
                "state": (r["symbol"], side, ts_key),
            })

    for r in stop_results:
        ts_key = r["timestamp"].strftime("%Y%m%d%H%M")
        signal_type = r["signal"]

        if not is_signal_reported(r["symbol"], signal_type, ts_key):
            candidates.append({
                "type": "STOP",
                "result": r,
                "state": (r["symbol"], signal_type, ts_key),
            })

    if not candidates:
        return None, []

    lines = []

    for item in candidates:
        r = item["result"]

        if item["type"] == "RROF":
            # Format RROF: long XAU-USDT 2650.30 12.45 15.20
            lines.append(
                f"{r['signal'].lower()} "
                f"{r['symbol']} "
                f"{r['price']:.2f} "
                f"{r['signal_line']:.2f} "
                f"{r['rrof_s']:.2f}"
            )
        else:
            # Format Stop Hunt: long XYZUSDT swp=12.34% rcv=13.21%
            side = "long" if r["side"] == "LONG" else "short"
            lines.append(
                f"{side} {clean_symbol(r['symbol'])} "
                f"swp={r['sweep_pct']:.2f}% "
                f"rcv={r['recover_pct']:.2f}%"
            )

    return "\n".join(lines), candidates


def commit_sent_states(candidates):
    for item in candidates:
        symbol, signal_type, ts_key = item["state"]
        mark_signal_reported(symbol, signal_type, ts_key)


# ============================================================
# RUN ONCE
# ============================================================

def run_once():
    started = time.time()

    print("=" * 70)
    print(
        "🚀 OKX SCANNER START | "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )
    print("=" * 70)
    print(f"RROF symbols : {MANUAL_SYMBOLS}")
    print(f"RROF TF      : {TIMEFRAME}")
    print(f"Top movers   : {TOP_GAINERS_COUNT} + {TOP_LOSERS_COUNT}")
    print(f"Stop Hunt TF : {TIMEFRAME}")
    print(f"Volume min   : ${MIN_VOLUME_24H_USD / 1e6:.1f}M")
    print(f"Stop Hunt    : sweep>={STOP_HUNT_SWEEP_PCT}% recover>={STOP_HUNT_RECOVER_PCT}%")
    print("=" * 70)

    rrof_results = []
    stop_results = []

    try:
        rrof_results = scan_manual_rrof()
    except Exception as e:
        print(f"❌ RROF section error: {e}")

    try:
        stop_results = scan_top_movers_stophunt()
    except Exception as e:
        print(f"❌ Stop Hunt section error: {e}")

    message, candidates = build_messages(
        rrof_results,
        stop_results,
    )

    print("\n" + "=" * 70)
    print(f"RROF signals : {len(rrof_results)}")
    print(f"Stop Hunt    : {len(stop_results)}")
    print("=" * 70)

    if message:
        print("\n📨 TELEGRAM:")
        print(message)

        # Chỉ commit state sau khi Telegram thành công.
        if send_telegram(message):
            commit_sent_states(candidates)
            print("✅ Telegram OK + state saved")
        else:
            print("⚠️ Telegram fail → state KHÔNG được đánh dấu")
    else:
        print("ℹ️ Không có tín hiệu mới.")

    elapsed = time.time() - started
    print(f"\n✅ FINISHED in {elapsed:.1f}s")


if __name__ == "__main__":
    try:
        run_once()
    except KeyboardInterrupt:
        print("⛔ Stopped")
        raise SystemExit(0)
    except Exception as e:
        print(f"❌ FATAL ERROR: {e}")
        raise SystemExit(1)
