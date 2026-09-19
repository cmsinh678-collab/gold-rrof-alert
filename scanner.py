import os
import sys
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime
import json
import concurrent.futures

# ============================================================
# CONFIG
# ============================================================

QUIET_TOP_MOVERS = True

# Danh sách symbol thủ công — CHỈ chạy RROF
MANUAL_SYMBOLS = ["XAU-USDT", "ETH-USDT"]

TIMEFRAME = "15m"
CANDLE_LIMIT = 200

# Thời gian chờ giữa các vòng lặp (giây)
LOOP_INTERVAL = 900   # 15 phút

# Số thread song song khi quét Stop Hunt
STOP_HUNT_MAX_WORKERS = 10

# OKX API
OKX_BASE_URL = "https://www.okx.com"
OKX_CANDLES_URL = f"{OKX_BASE_URL}/api/v5/market/history-candles"
OKX_INSTRUMENTS_URL = f"{OKX_BASE_URL}/api/v5/public/instruments"
OKX_TICKERS_URL = f"{OKX_BASE_URL}/api/v5/market/tickers"

# ============================================================
# EVEREX / RROF SETTINGS  (dùng cho XAU/ETH)
# ============================================================

RROF_LENGTH = 10
RROF_MA_TYPE = "WMA"
SMOOTH = 3
SIGNAL_LENGTH = 5
SIGNAL_MA_TYPE = "WMA"
LOOKBACK = 20
LOOKBACK_MA_TYPE = "SMA"

# ============================================================
# STOP HUNT SETTINGS  (dùng cho top movers)
# ============================================================

STOP_HUNT_SWEEP_PCT = 0.10
STOP_HUNT_RECOVER_PCT = 0.10
STOP_HUNT_REQUIRE_CLOSE_ABOVE_OPEN = True
STOP_HUNT_VOLUME_MULT = 0.0
STOP_HUNT_VOLUME_LOOKBACK = 20

# Multi-bar Stop Hunt
STOP_HUNT_MULTIBAR_ENABLED = True
STOP_HUNT_MAX_LOOKBACK_BARS = 3

# ============================================================
# TOP MOVERS SETTINGS
# ============================================================

TOP_GAINERS_COUNT = 50
TOP_LOSERS_COUNT = 50
MIN_VOLUME_24H_USD = 20_000_000
TOP_MOVERS_TIMEFRAME = "1H"
TOP_MOVERS_CACHE_TTL = 300
TOP_MOVERS_MAX_WORKERS = 15
AUTO_SCAN_INST_TYPE = "SWAP"
AUTO_SCAN_QUOTE = "USDT"

# ============================================================
# TELEGRAM
# ============================================================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

STATE_FILE = "signal_state.json"

# ============================================================
# STATE MANAGEMENT
# ============================================================

def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def is_signal_reported(symbol, signal_type, timestamp):
    state = load_state()
    key = f"{symbol}_{signal_type}_{timestamp}"
    return state.get(key, False)

def mark_signal_reported(symbol, signal_type, timestamp):
    state = load_state()
    key = f"{symbol}_{signal_type}_{timestamp}"
    state[key] = True
    save_state(state)

# ============================================================
# TELEGRAM FUNCTIONS
# ============================================================

def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("⚠️ Không gửi Telegram (thiếu cấu hình):")
        print(message)
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {"chat_id": CHAT_ID, "text": message}

    try:
        response = requests.post(url, json=data, timeout=20)
        print(f"📨 Telegram HTTP: {response.status_code}")
        if response.status_code != 200:
            print(f"❌ Telegram response: {response.text}")
            return False
        print("✅ Telegram sent")
        return True
    except Exception as e:
        print(f"❌ Telegram error: {e}")
        return False

# ============================================================
# OKX SYMBOL FINDER
# ============================================================

def find_symbol(search_term):
    print()
    print(f"🔍 Đang tìm {search_term} trên OKX...")

    inst_types = ["SWAP", "SPOT"]

    for inst_type in inst_types:
        try:
            params = {"instType": inst_type}
            headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

            response = requests.get(OKX_INSTRUMENTS_URL, params=params, headers=headers, timeout=15)
            if response.status_code != 200:
                continue

            data = response.json()
            if data.get('code') != '0':
                continue

            for inst in data['data']:
                inst_id = inst.get('instId', '')
                if search_term in inst_id:
                    print(f"✅ Tìm thấy {inst_type}: {inst_id}")
                    return inst_id, inst_type
        except Exception as e:
            print(f"⚠️ Lỗi khi kiểm tra {inst_type}: {e}")
            continue

    print(f"❌ Không tìm thấy {search_term} trên OKX")
    return None, None

# ============================================================
# TOP MOVERS
# ============================================================

_top_movers_cache = {
    "timestamp": 0,
    "gainers": [],
    "losers": [],
    "all": []
}


def _fetch_okx_tickers(inst_type):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    }
    params = {"instType": inst_type}
    try:
        r = requests.get(OKX_TICKERS_URL, params=params, headers=headers, timeout=20)
    except requests.RequestException as e:
        print(f"⚠️ Không lấy được tickers {inst_type}: {e}")
        return []

    if r.status_code != 200:
        print(f"⚠️ OKX tickers HTTP {r.status_code}")
        return []

    try:
        data = r.json()
    except Exception:
        return []

    if data.get("code") != "0":
        print(f"⚠️ OKX tickers error: {data.get('msg')}")
        return []

    return data.get("data", [])


def _get_change_pct_1h(inst_id, tf="1H"):
    params = {"instId": inst_id, "bar": tf, "limit": "2"}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    }
    try:
        r = requests.get(OKX_CANDLES_URL, params=params, headers=headers, timeout=10)
        if r.status_code != 200:
            return inst_id, None
        data = r.json()
        if data.get("code") != "0":
            return inst_id, None
        candles = data.get("data", [])
        if len(candles) < 2:
            return inst_id, None

        last_close = float(candles[0][4])
        prev_close = float(candles[1][4])
        if prev_close <= 0:
            return inst_id, None
        pct = (last_close - prev_close) / prev_close * 100
        return inst_id, pct
    except Exception:
        return inst_id, None


def get_top_movers(force_refresh=False):
    global _top_movers_cache

    now = time.time()
    if (not force_refresh
            and _top_movers_cache["all"]
            and now - _top_movers_cache["timestamp"] < TOP_MOVERS_CACHE_TTL):
        print(f"📦 Dùng cache top movers: {len(_top_movers_cache['all'])} symbols "
              f"(còn {int(TOP_MOVERS_CACHE_TTL - (now - _top_movers_cache['timestamp']))}s)")
        return (_top_movers_cache["gainers"],
                _top_movers_cache["losers"],
                _top_movers_cache["all"])

    print(f"🔎 Đang lấy tickers {AUTO_SCAN_INST_TYPE} từ OKX...")
    tickers = _fetch_okx_tickers(AUTO_SCAN_INST_TYPE)
    if not tickers:
        print("❌ Không lấy được tickers, dùng cache cũ nếu có")
        return (_top_movers_cache["gainers"],
                _top_movers_cache["losers"],
                _top_movers_cache["all"])

    candidates = []
    for t in tickers:
        inst_id = t.get("instId", "")
        if not inst_id.endswith(f"-{AUTO_SCAN_QUOTE}"):
            continue
        try:
            last = float(t.get("last") or 0)
            vol_ccy_24h = float(t.get("volCcy24h") or 0)
            vol_24h = float(t.get("vol24h") or 0)
        except (TypeError, ValueError):
            continue
        usd_volume = max(vol_ccy_24h, vol_24h * last)
        if usd_volume < MIN_VOLUME_24H_USD:
            continue
        candidates.append(inst_id)

    print(f"✅ Sau lọc volume ≥ ${MIN_VOLUME_24H_USD/1e6:.1f}M: {len(candidates)} coin")
    print(f"⏳ Đang lấy % thay đổi {TOP_MOVERS_TIMEFRAME} cho từng coin...")

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=TOP_MOVERS_MAX_WORKERS) as executor:
        futures = [executor.submit(_get_change_pct_1h, c, TOP_MOVERS_TIMEFRAME)
                   for c in candidates]
        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            inst_id, pct = fut.result()
            if pct is not None:
                results.append({"instId": inst_id, "pct_1h": pct})
            if i % 50 == 0:
                print(f"   ... {i}/{len(candidates)}")

    if not results:
        print("❌ Không lấy được % thay đổi coin nào")
        return ([], [], [])

    results.sort(key=lambda x: x["pct_1h"], reverse=True)

    n_gain = min(TOP_GAINERS_COUNT, len(results))
    n_lose = min(TOP_LOSERS_COUNT, len(results))

    gainers = results[:n_gain]
    losers = list(reversed(results[-n_lose:]))

    gainers_ids = [x["instId"] for x in gainers]
    losers_ids = [x["instId"] for x in losers]

    all_symbols = list(dict.fromkeys(gainers_ids + losers_ids))

    _top_movers_cache["timestamp"] = now
    _top_movers_cache["gainers"] = gainers_ids
    _top_movers_cache["losers"] = losers_ids
    _top_movers_cache["all"] = all_symbols

    print()
    print(f"{'='*70}")
    print(f"🚀 TOP {n_gain} TĂNG MẠNH NHẤT {TOP_MOVERS_TIMEFRAME}:")
    print(f"{'='*70}")
    for i, x in enumerate(gainers[:15], 1):
        print(f"   {i:2d}. {x['instId']:<24} {x['pct_1h']:+.2f}%")
    if n_gain > 15:
        print(f"   ... và {n_gain - 15} coin khác")

    print()
    print(f"{'='*70}")
    print(f"💥 TOP {n_lose} GIẢM MẠNH NHẤT {TOP_MOVERS_TIMEFRAME}:")
    print(f"{'='*70}")
    for i, x in enumerate(losers[:15], 1):
        print(f"   {i:2d}. {x['instId']:<24} {x['pct_1h']:+.2f}%")
    if n_lose > 15:
        print(f"   ... và {n_lose - 15} coin khác")

    print()
    print(f"📋 Tổng symbol top movers (đã dedupe): {len(all_symbols)}")

    return gainers_ids, losers_ids, all_symbols

# ============================================================
# GET OKX CANDLES
# ============================================================

def get_okx_candles(symbol, inst_type="SPOT", quiet=False):
    if not quiet:
        print()
        print("=" * 70)
        print(f"📥 OKX {symbol} ({inst_type})")
        print("=" * 70)

    params = {
        "instId": symbol,
        "bar": TIMEFRAME,
        "limit": str(CANDLE_LIMIT)
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    }

    try:
        response = requests.get(OKX_CANDLES_URL, params=params, headers=headers, timeout=30)
    except requests.RequestException as e:
        raise Exception(f"OKX connection error: {e}")

    if response.status_code != 200:
        if not quiet:
            print(response.text)
        raise Exception(f"OKX HTTP error {response.status_code}")

    try:
        data = response.json()
    except Exception:
        raise Exception("OKX trả về dữ liệu không phải JSON")

    if data.get("code") != "0":
        raise Exception(f"OKX API Error: {data.get('msg', 'Unknown error')}")

    candles = data.get("data", [])
    if not candles:
        raise Exception("OKX không trả về candles")

    rows = []
    for candle in candles:
        if len(candle) < 9:
            continue
        rows.append({
            "timestamp": candle[0], "open": candle[1], "high": candle[2],
            "low": candle[3], "close": candle[4], "volume": candle[5],
            "volume_currency": candle[6], "volume_quote": candle[7],
            "confirm": candle[8]
        })

    if not rows:
        raise Exception("Không parse được dữ liệu OKX")

    df = pd.DataFrame(rows)
    for column in ["open","high","low","close","volume","volume_currency","volume_quote"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df["timestamp"] = pd.to_datetime(pd.to_numeric(df["timestamp"], errors="coerce"), unit="ms", utc=True)
    df = df.sort_values("timestamp").drop_duplicates(subset=["timestamp"]).reset_index(drop=True)
    df = df.dropna(subset=["timestamp","open","high","low","close","volume"])

    if not quiet:
        print(f"✅ Lấy thành công {len(df)} candles")
        print(f"💰 Last price: {df.iloc[-1]['close']:.2f}")
        print(f"🕯 Nến cuối confirm: {df.iloc[-1]['confirm']}")

    return df

# ============================================================
# MOVING AVERAGE FUNCTIONS
# ============================================================

def wma(series, length):
    weights = np.arange(1, length + 1)
    return series.rolling(length).apply(
        lambda x: np.dot(x, weights) / weights.sum(),
        raw=True
    )

def get_average(series, length, ma_type):
    ma_type = ma_type.upper()
    if ma_type == "SMA":
        return series.rolling(length).mean()
    elif ma_type == "EMA":
        return series.ewm(span=length, adjust=False).mean()
    elif ma_type == "RMA":
        return series.ewm(alpha=1 / length, adjust=False).mean()
    elif ma_type == "WMA":
        return wma(series, length)
    else:
        raise ValueError(f"Unsupported MA type: {ma_type}")

# ============================================================
# EVEREX NORMALIZE
# ============================================================

def normalize(value, average):
    average = average.replace(0, np.nan)
    x = value / average

    result = np.select(
        [x > 1.50, x > 1.20, x > 1.00, x > 0.80, x > 0.60, x > 0.40, x > 0.20],
        [1.00, 0.90, 0.80, 0.70, 0.60, 0.50, 0.25],
        default=0.10
    )

    return pd.Series(result, index=value.index)

# ============================================================
# EVEREX / RROF CALCULATION
# ============================================================

def calculate_everex(df):
    df = df.copy()

    open_ = df["open"]
    high = df["high"]
    low = df["low"]
    close = df["close"]
    volume = df["volume"]

    vola = get_average(volume, LOOKBACK, LOOKBACK_MA_TYPE)
    vola_n = normalize(volume, vola) * 100

    bar_spread = close - open_
    bar_range = high - low
    bar_range = bar_range.replace(0, np.nan)

    r2 = high.rolling(2).max() - low.rolling(2).min()
    r2 = r2.replace(0, np.nan)

    src_shift = close.diff()
    sign_spread = np.sign(bar_spread)
    sign_shift = np.sign(src_shift)

    barclosing = (2 * (close - low) / bar_range * 100) - 100
    s2r = bar_spread / bar_range * 100

    bar_spread_abs = abs(bar_spread)
    bar_spread_avg = get_average(bar_spread_abs, LOOKBACK, LOOKBACK_MA_TYPE)
    bar_spread_ratio_n = normalize(bar_spread_abs, bar_spread_avg) * 100 * sign_spread

    low2 = low.rolling(2).min()
    barclosing_2 = (2 * (close - low2) / r2 * 100) - 100
    shift2bar_to_r2 = src_shift / r2 * 100

    src_shift_abs = abs(src_shift)
    srcshift_avg = get_average(src_shift_abs, LOOKBACK, LOOKBACK_MA_TYPE)
    srcshift_ratio_n = normalize(src_shift_abs, srcshift_avg) * 100 * sign_shift

    pricea_n = (barclosing + s2r + bar_spread_ratio_n + barclosing_2 + shift2bar_to_r2 + srcshift_ratio_n) / 6
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
# STOP HUNT DETECTION
# ============================================================

def _check_bull_rejection(o_swing, l_swing, c_now):
    if o_swing <= 0 or l_swing <= 0:
        return None

    sweep_pct = (o_swing - l_swing) / o_swing * 100
    recover_pct = (c_now - l_swing) / l_swing * 100

    if sweep_pct < STOP_HUNT_SWEEP_PCT * 100:
        return None
    if recover_pct < STOP_HUNT_RECOVER_PCT * 100:
        return None

    if STOP_HUNT_REQUIRE_CLOSE_ABOVE_OPEN and c_now <= o_swing:
        return None

    return sweep_pct, recover_pct


def _check_bear_rejection(o_swing, h_swing, c_now):
    if o_swing <= 0 or h_swing <= 0:
        return None

    sweep_pct = (h_swing - o_swing) / o_swing * 100
    recover_pct = (h_swing - c_now) / h_swing * 100

    if sweep_pct < STOP_HUNT_SWEEP_PCT * 100:
        return None
    if recover_pct < STOP_HUNT_RECOVER_PCT * 100:
        return None

    if STOP_HUNT_REQUIRE_CLOSE_ABOVE_OPEN and c_now >= o_swing:
        return None

    return sweep_pct, recover_pct


def detect_stop_hunt(df, symbol_name, quiet=False):
    """
    Phát hiện Stop Hunt. Tham số quiet=True để tắt log chi tiết.
    """
    result = {
        'symbol': symbol_name,
        'signal': None,
        'side': None,
        'price': None,
        'open': None,
        'high': None,
        'low': None,
        'close': None,
        'sweep_pct': None,
        'recover_pct': None,
        'volume': None,
        'timestamp': None,
        'bars': None,
    }

    if len(df) < STOP_HUNT_VOLUME_LOOKBACK + 2:
        return result

    confirmed = df[df["confirm"].astype(str) == "1"].copy()
    if len(confirmed) < 2:
        return result

    # Volume filter
    last_bar = confirmed.iloc[-1]
    v = float(last_bar['volume'])
    if STOP_HUNT_VOLUME_MULT > 0:
        avg_vol = confirmed['volume'].iloc[-(STOP_HUNT_VOLUME_LOOKBACK + 1):-1].mean()
        if pd.notna(avg_vol) and avg_vol > 0:
            if v < avg_vol * STOP_HUNT_VOLUME_MULT:
                if not quiet:
                    print(f"🚫 {symbol_name}: volume thấp hơn {STOP_HUNT_VOLUME_MULT}x avg")
                return result

    max_lookback = STOP_HUNT_MAX_LOOKBACK_BARS if STOP_HUNT_MULTIBAR_ENABLED else 1
    max_lookback = min(max_lookback, len(confirmed))

    for bars in range(1, max_lookback + 1):
        window = confirmed.iloc[-bars:]

        first = window.iloc[0]
        last = window.iloc[-1]

        o_swing = float(first['open'])
        c_now = float(last['close'])
        ts_last = last['timestamp']

        l_swing = float(window['low'].min())
        h_swing = float(window['high'].max())

        # BULLISH
        bull = _check_bull_rejection(o_swing, l_swing, c_now)
        if bull:
            sweep_pct, recover_pct = bull
            ts_key = ts_last.strftime('%Y%m%d%H%M')
            key_type = f'BULL_STOP_HUNT_{bars}bar'

            if not is_signal_reported(symbol_name, key_type, ts_key):
                print(f"🟢 {symbol_name}: BULLISH STOP HUNT ({bars} nến) | "
                      f"sập {sweep_pct:.2f}% | hồi {recover_pct:.2f}%")
                mark_signal_reported(symbol_name, key_type, ts_key)
                result.update({
                    'signal': 'BULL_STOP_HUNT',
                    'side': 'LONG',
                    'price': c_now,
                    'open': o_swing,
                    'high': h_swing,
                    'low': l_swing,
                    'close': c_now,
                    'sweep_pct': sweep_pct,
                    'recover_pct': recover_pct,
                    'volume': v,
                    'timestamp': ts_last,
                    'bars': bars,
                })
                return result

        # BEARISH
        bear = _check_bear_rejection(o_swing, h_swing, c_now)
        if bear:
            sweep_pct, recover_pct = bear
            ts_key = ts_last.strftime('%Y%m%d%H%M')
            key_type = f'BEAR_STOP_HUNT_{bars}bar'

            if not is_signal_reported(symbol_name, key_type, ts_key):
                print(f"🔴 {symbol_name}: BEARISH STOP HUNT ({bars} nến) | "
                      f"vọt {sweep_pct:.2f}% | rơi {recover_pct:.2f}%")
                mark_signal_reported(symbol_name, key_type, ts_key)
                result.update({
                    'signal': 'BEAR_STOP_HUNT',
                    'side': 'SHORT',
                    'price': c_now,
                    'open': o_swing,
                    'high': h_swing,
                    'low': l_swing,
                    'close': c_now,
                    'sweep_pct': sweep_pct,
                    'recover_pct': recover_pct,
                    'volume': v,
                    'timestamp': ts_last,
                    'bars': bars,
                })
                return result

    if not quiet:
        print(f"🚫 {symbol_name}: Không có Stop Hunt")
    return result

# ============================================================
# SIGNAL CHECK - RROF CROSSOVER
# ============================================================

def check_signal(df, symbol_name):
    result = {
        'symbol': symbol_name,
        'signal': None,
        'price': None,
        'timestamp': None,
        'rrof': None,
        'signal_line': None,
        'volume': None,
        'signal_timestamp': None
    }

    if len(df) < 20:
        print(f"⚠️ {symbol_name}: Không đủ dữ liệu")
        return result

    confirmed = df[df["confirm"].astype(str) == "1"].copy()

    if len(confirmed) < 2:
        print(f"⚠️ {symbol_name}: Không tìm đủ 2 nến đã đóng")
        return result

    td1 = confirmed.iloc[-1]
    td2 = confirmed.iloc[-2]

    print(f"📊 TD2 (nến -2): {td2['timestamp']} | RROF_S={td2['RROF_S']:.2f}, SIGNAL={td2['SIGNAL']:.2f}")
    print(f"📊 TD1 (nến -1): {td1['timestamp']} | RROF_S={td1['RROF_S']:.2f}, SIGNAL={td1['SIGNAL']:.2f}")

    ht = df.iloc[-1]
    print(f"🕯 Nến HT (đang mở): {ht['timestamp']} | confirm={ht['confirm']}")

    result['price'] = float(td1['close'])
    result['timestamp'] = td1['timestamp']
    result['rrof'] = float(td1['RROF'])
    result['signal_line'] = float(td1['SIGNAL'])
    result['volume'] = float(td1['volume'])
    result['signal_timestamp'] = td1['timestamp']

    if td2["RROF_S"] <= td2["SIGNAL"] and td1["RROF_S"] > td1["SIGNAL"]:
        timestamp_key = td1['timestamp'].strftime('%Y%m%d%H%M')
        if not is_signal_reported(symbol_name, 'LONG', timestamp_key):
            print(f"🟢 {symbol_name}: CẮT LÊN (LONG) tại nến TD1 {td1['timestamp']}")
            result['signal'] = 'LOG'
            mark_signal_reported(symbol_name, 'LONG', timestamp_key)
            return result
        else:
            print(f"ℹ️ {symbol_name}: Tín hiệu LONG đã báo trước đó")
            return result

    if td2["RROF_S"] >= td2["SIGNAL"] and td1["RROF_S"] < td1["SIGNAL"]:
        timestamp_key = td1['timestamp'].strftime('%Y%m%d%H%M')
        if not is_signal_reported(symbol_name, 'SHORT', timestamp_key):
            print(f"🔴 {symbol_name}: CẮT XUỐNG (SHORT) tại nến TD1 {td1['timestamp']}")
            result['signal'] = 'SHO'
            mark_signal_reported(symbol_name, 'SHORT', timestamp_key)
            return result
        else:
            print(f"ℹ️ {symbol_name}: Tín hiệu SHORT đã báo trước đó")
            return result

    print(f"🚫 {symbol_name}: Không có cắt giữa TD2 và TD1")
    return result

# ============================================================
# BUILD TELEGRAM MESSAGE
# ============================================================

def _clean_symbol(inst_id):
    s = inst_id
    if s.endswith("-SWAP"):
        s = s[:-5]
    s = s.replace("-", "")
    return s


def build_message(rrof_results, stop_hunt_results):
    lines = []

    rrof_signals = [r for r in rrof_results if r.get('signal') is not None]
    for s in rrof_signals:
        line = f"{s['signal'].lower()} {s['symbol']} {s['price']:.2f} {s['signal_line']:.2f} {s['rrof']:.2f}"
        lines.append(line)

    sh_signals = [r for r in stop_hunt_results if r.get('signal') is not None]
    for s in sh_signals:
        side = "long" if s['side'] == "LONG" else "short"
        clean_sym = _clean_symbol(s['symbol'])
        line = f"{side} {clean_sym} sweep={s['sweep_pct']:.2f}% rcv={s['recover_pct']:.2f}%"
        lines.append(line)

    if not lines:
        return None
    return "\n".join(lines)

# ============================================================
# SCAN XAU/ETH — CHỈ RROF
# ============================================================

def scan_manual_rrof():
    print(f"\n{'#'*70}")
    print(f"# PHẦN 1: QUÉT XAU/ETH — CHỈ RROF")
    print(f"{'#'*70}")

    results = []

    for search_term in MANUAL_SYMBOLS:
        print(f"\n{'='*70}")
        print(f"🔍 Đang quét: {search_term}")
        print('='*70)

        found_symbol, inst_type = find_symbol(search_term)
        if not found_symbol:
            print(f"❌ Không tìm thấy {search_term} trên OKX")
            continue

        try:
            df = get_okx_candles(found_symbol, inst_type)
        except Exception as e:
            print(f"❌ DATA ERROR for {search_term}: {e}")
            continue

        if df is None:
            continue

        print("🧮 Calculating EVEREX / RROF...")
        try:
            df = calculate_everex(df)
        except Exception as e:
            print(f"❌ EVEREX ERROR: {e}")
            continue

        result = check_signal(df, found_symbol)
        results.append(result)

    return results

# ============================================================
# SCAN TOP MOVERS — CHỈ STOP HUNT
# ============================================================

def _scan_one_stophunt(inst_id):
    inst_type = "SWAP" if inst_id.endswith("-SWAP") else "SPOT"
    try:
        df = get_okx_candles(inst_id, inst_type, quiet=QUIET_TOP_MOVERS)
        if df is None:
            return None
        return detect_stop_hunt(df, inst_id, quiet=QUIET_TOP_MOVERS)
    except Exception as e:
        print(f"❌ {inst_id}: {e}")
        return None


def scan_top_movers_stophunt():
    print(f"\n{'#'*70}")
    print(f"# PHẦN 2: QUÉT TOP MOVERS — CHỈ STOP HUNT (song song)")
    print(f"{'#'*70}")

    gainers, losers, top_symbols = get_top_movers()

    if not top_symbols:
        print("❌ Không có symbol top movers nào")
        return []

    print(f"\n⚡ Quét song song {len(top_symbols)} coin với "
          f"{STOP_HUNT_MAX_WORKERS} thread...")

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=STOP_HUNT_MAX_WORKERS) as executor:
        futures = {executor.submit(_scan_one_stophunt, sym): sym for sym in top_symbols}
        for i, fut in enumerate(concurrent.futures.as_completed(futures), 1):
            r = fut.result()
            if r is not None:
                results.append(r)
            if i % 20 == 0:
                print(f"   ... {i}/{len(top_symbols)}")

    return results

# ============================================================
# RUN ONCE
# ============================================================

def run_once():
    print()
    print("=" * 70)
    print(f"🚀 SCAN START — {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 70)
    print(f"Timeframe       : {TIMEFRAME}")
    print(f"XAU/ETH logic   : RROF only  ({MANUAL_SYMBOLS})")
    print(f"Top movers logic: Stop Hunt only "
          f"({TOP_GAINERS_COUNT} gainers + {TOP_LOSERS_COUNT} losers {TOP_MOVERS_TIMEFRAME})")
    print(f"Volume min      : ${MIN_VOLUME_24H_USD/1e6:.1f}M (24h)")
    print(f"Stop Hunt params: sweep≥{STOP_HUNT_SWEEP_PCT*100:.1f}%, "
          f"recover≥{STOP_HUNT_RECOVER_PCT*100:.1f}%")
    print(f"Multi-bar       : {'ON' if STOP_HUNT_MULTIBAR_ENABLED else 'OFF'} "
          f"(max {STOP_HUNT_MAX_LOOKBACK_BARS} nến)")
    print("=" * 70)

    # PHẦN 1: XAU/ETH → RROF
    t0 = time.time()
    rrof_results = scan_manual_rrof()
    print(f"\n⏱ PHẦN 1 (XAU/ETH): {time.time()-t0:.1f}s")

    # PHẦN 2: TOP MOVERS → STOP HUNT
    t1 = time.time()
    stop_hunt_results = scan_top_movers_stophunt()
    print(f"\n⏱ PHẦN 2 (top movers): {time.time()-t1:.1f}s")

    # Tổng kết
    rrof_sig_count = len([r for r in rrof_results if r.get('signal')])
    sh_sig_count = len([r for r in stop_hunt_results if r.get('signal')])

    print()
    print("=" * 70)
    print(f"📊 KẾT QUẢ VÒNG NÀY:")
    print(f"   RROF signals (XAU/ETH)         : {rrof_sig_count}")
    print(f"   Stop Hunt signals (top movers) : {sh_sig_count}")
    print("=" * 70)

    message = build_message(rrof_results, stop_hunt_results)

    if message:
        print("\n📨 Sending Telegram...")
        send_telegram(message)
    else:
        print("\nℹ️ Không có tín hiệu mới. Không gửi Telegram.")

    print("\n✅ Scan completed.")

# ============================================================
# ENTRY POINT — LOOP 15 PHÚT
# ============================================================

if __name__ == "__main__":
    print()
    print("🤖 BOT KHỞI ĐỘNG")
    print(f"⏰ Sẽ chạy mỗi {LOOP_INTERVAL//60} phút")
    print(f"🔍 Multi-bar Stop Hunt: {'BẬT' if STOP_HUNT_MULTIBAR_ENABLED else 'TẮT'}")
    print()

    while True:
        try:
            run_once()
        except KeyboardInterrupt:
            print("\n⛔ Dừng bởi người dùng.")
            sys.exit(0)
        except Exception as e:
            print(f"\n❌ LỖI VÒNG LẶP: {e}")

        print(f"\n⏰ Chờ {LOOP_INTERVAL//60} phút rồi chạy lại...")
        try:
            time.sleep(LOOP_INTERVAL)
        except KeyboardInterrupt:
            print("\n⛔ Dừng bởi người dùng.")
            sys.exit(0)
