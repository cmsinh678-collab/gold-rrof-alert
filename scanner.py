import os
import sys
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime

# ============================================================
# CONFIG
# ============================================================

# Symbol mặc định, sẽ được tự động tìm nếu không hoạt động
SYMBOL = "XAU-USDT"  # Ưu tiên dùng spot trước

TIMEFRAME = "15m"
CANDLE_LIMIT = 200

# OKX API
OKX_BASE_URL = "https://www.okx.com"
OKX_CANDLES_URL = f"{OKX_BASE_URL}/api/v5/market/history-candles"
OKX_INSTRUMENTS_URL = f"{OKX_BASE_URL}/api/v5/public/instruments"

# ============================================================
# EVEREX / RROF SETTINGS
# ============================================================

RROF_LENGTH = 10
RROF_MA_TYPE = "WMA"
SMOOTH = 3
SIGNAL_LENGTH = 5
SIGNAL_MA_TYPE = "WMA"
LOOKBACK = 20
LOOKBACK_MA_TYPE = "SMA"

# ============================================================
# TELEGRAM
# ============================================================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ============================================================
# TELEGRAM FUNCTIONS
# ============================================================

def check_telegram_config():
    if not BOT_TOKEN:
        print("⚠️ TELEGRAM_BOT_TOKEN chưa được thiết lập")
    if not CHAT_ID:
        print("⚠️ TELEGRAM_CHAT_ID chưa được thiết lập")

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

def find_xau_symbol():
    """Tìm symbol XAU/USD trên OKX (ưu tiên Swap, sau đó Spot)"""
    print()
    print("🔍 Đang tìm symbol XAU/USD trên OKX...")
    
    # Danh sách các loại instrument cần kiểm tra
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
                # Tìm symbol chứa XAU
                if 'XAU' in inst_id:
                    print(f"✅ Tìm thấy {inst_type}: {inst_id}")
                    return inst_id, inst_type
        except Exception as e:
            print(f"⚠️ Lỗi khi kiểm tra {inst_type}: {e}")
            continue
    
    print("❌ Không tìm thấy symbol XAU/USD nào trên OKX")
    return None, None

# ============================================================
# GET OKX CANDLES
# ============================================================

def get_okx_candles(symbol, inst_type="SPOT"):
    """Lấy dữ liệu nến từ OKX"""
    print()
    print("=" * 70)
    print(f"📥 OKX {symbol} ({inst_type})")
    print("=" * 70)
    print(f"Symbol    : {symbol}")
    print(f"Timeframe : {TIMEFRAME}")
    print(f"Limit     : {CANDLE_LIMIT}")

    params = {
        "instId": symbol,
        "bar": TIMEFRAME,
        "limit": str(CANDLE_LIMIT)
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json"
    }

    print()
    print("▶️ Calling OKX API...")

    try:
        response = requests.get(OKX_CANDLES_URL, params=params, headers=headers, timeout=30)
    except requests.RequestException as e:
        raise Exception(f"OKX connection error: {e}")

    print(f"HTTP Status: {response.status_code}")

    if response.status_code != 200:
        print(response.text)
        raise Exception(f"OKX HTTP error {response.status_code}")

    try:
        data = response.json()
    except Exception:
        print(response.text)
        raise Exception("OKX trả về dữ liệu không phải JSON")

    if data.get("code") != "0":
        raise Exception(f"OKX API Error: {data.get('msg', 'Unknown error')}")

    candles = data.get("data", [])
    if not candles:
        raise Exception("OKX không trả về candles")

    print(f"✅ Raw candles: {len(candles)}")

    # OKX CANDLE FORMAT:
    # [timestamp, open, high, low, close, volume, volume_currency, volume_quote, confirm]

    rows = []
    for candle in candles:
        if len(candle) < 9:
            continue
        rows.append({
            "timestamp": candle[0],
            "open": candle[1],
            "high": candle[2],
            "low": candle[3],
            "close": candle[4],
            "volume": candle[5],
            "volume_currency": candle[6],
            "volume_quote": candle[7],
            "confirm": candle[8]
        })

    if not rows:
        raise Exception("Không parse được dữ liệu OKX")

    df = pd.DataFrame(rows)

    # Convert numeric columns
    numeric_columns = ["open", "high", "low", "close", "volume", "volume_currency", "volume_quote"]
    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    # Convert timestamp
    df["timestamp"] = pd.to_datetime(pd.to_numeric(df["timestamp"], errors="coerce"), unit="ms", utc=True)

    # Sort and deduplicate
    df = df.sort_values("timestamp")
    df = df.drop_duplicates(subset=["timestamp"])
    df = df.reset_index(drop=True)

    # Remove invalid rows
    df = df.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])

    # Volume check
    print()
    print("=" * 70)
    print("🔊 OKX VOLUME CHECK")
    print("=" * 70)
    print(f"Volume min : {df['volume'].min():,.4f}")
    print(f"Volume max : {df['volume'].max():,.4f}")
    print(f"Volume avg : {df['volume'].mean():,.4f}")
    print(f"Zero       : {(df['volume'] == 0).sum()}")

    if (df["volume"] <= 0).all():
        raise Exception("Volume OKX không hợp lệ")

    # Candle status
    print()
    print("=" * 70)
    print("🕯 CANDLE STATUS")
    print("=" * 70)
    print(f"Latest confirm: {df.iloc[-1]['confirm']}")
    print("0 = chưa đóng, 1 = đã đóng")

    # Last candles
    print()
    print("=" * 70)
    print("📋 LAST 5 CANDLES")
    print("=" * 70)
    print(df.tail(5)[["timestamp", "open", "high", "low", "close", "volume", "confirm"]].to_string(index=False))

    print()
    print(f"💰 Last price: {df.iloc[-1]['close']:.2f}")
    print(f"📊 Last volume: {df.iloc[-1]['volume']:,.4f}")

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
        [
            x > 1.50,
            x > 1.20,
            x > 1.00,
            x > 0.80,
            x > 0.60,
            x > 0.40,
            x > 0.20
        ],
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

    # VOLUME
    vola = get_average(volume, LOOKBACK, LOOKBACK_MA_TYPE)
    vola_n = normalize(volume, vola) * 100

    # PRICE
    bar_spread = close - open_
    bar_range = high - low
    bar_range = bar_range.replace(0, np.nan)

    # R2
    r2 = high.rolling(2).max() - low.rolling(2).min()
    r2 = r2.replace(0, np.nan)

    # SHIFT
    src_shift = close.diff()
    sign_spread = np.sign(bar_spread)
    sign_shift = np.sign(src_shift)

    # BAR CLOSING
    barclosing = (2 * (close - low) / bar_range * 100) - 100

    # SPREAD / RANGE
    s2r = bar_spread / bar_range * 100

    # SPREAD RATIO
    bar_spread_abs = abs(bar_spread)
    bar_spread_avg = get_average(bar_spread_abs, LOOKBACK, LOOKBACK_MA_TYPE)
    bar_spread_ratio_n = normalize(bar_spread_abs, bar_spread_avg) * 100 * sign_spread

    # 2 BAR CLOSING
    low2 = low.rolling(2).min()
    barclosing_2 = (2 * (close - low2) / r2 * 100) - 100

    # SHIFT / R2
    shift2bar_to_r2 = src_shift / r2 * 100

    # SHIFT RATIO
    src_shift_abs = abs(src_shift)
    srcshift_avg = get_average(src_shift_abs, LOOKBACK, LOOKBACK_MA_TYPE)
    srcshift_ratio_n = normalize(src_shift_abs, srcshift_avg) * 100 * sign_shift

    # PRICE NORMALIZED
    pricea_n = (barclosing + s2r + bar_spread_ratio_n + barclosing_2 + shift2bar_to_r2 + srcshift_ratio_n) / 6

    # BAR FLOW
    bar_flow = pricea_n * vola_n / 100

    # BULLS / BEARS
    bulls = bar_flow.clip(lower=0)
    bears = (-bar_flow.clip(upper=0))

    # BULLS / BEARS AVERAGE
    bulls_avg = get_average(bulls, RROF_LENGTH, RROF_MA_TYPE)
    bears_avg = get_average(bears, RROF_LENGTH, RROF_MA_TYPE)

    # RATIO
    bears_avg = bears_avg.replace(0, np.nan)
    dx = bulls_avg / bears_avg

    # RROF
    rrof = 2 * (100 - 100 / (1 + dx)) - 100

    # RROF SMOOTH
    rrof_s = get_average(rrof, SMOOTH, "WMA")

    # SIGNAL
    signal = get_average(rrof_s, SIGNAL_LENGTH, SIGNAL_MA_TYPE)

    # SAVE
    df["RROF"] = rrof
    df["RROF_S"] = rrof_s
    df["SIGNAL"] = signal

    return df

# ============================================================
# SIGNAL CHECK
# ============================================================

def check_signal(df):
    if len(df) < 20:
        print("⚠️ Không đủ dữ liệu")
        return None

    # Lấy 2 nến đã đóng gần nhất
    confirmed = df[df["confirm"].astype(str) == "1"].copy()

    if len(confirmed) < 2:
        print("⚠️ Không tìm đủ 2 nến đã đóng")
        return None

    previous = confirmed.iloc[-2]
    current = confirmed.iloc[-1]

    # STATUS
    print()
    print("=" * 70)
    print("📊 RROF STATUS")
    print("=" * 70)
    print(f"Previous candle : {previous['timestamp']}")
    print(f"Current candle  : {current['timestamp']}")
    print()
    print(f"Previous RROF_S : {previous['RROF_S']:.6f}")
    print(f"Previous SIGNAL : {previous['SIGNAL']:.6f}")
    print()
    print(f"Current RROF_S  : {current['RROF_S']:.6f}")
    print(f"Current SIGNAL  : {current['SIGNAL']:.6f}")
    print()
    print(f"Price           : {current['close']:.2f}")
    print(f"Volume          : {current['volume']:,.4f}")

    # CROSS UP (LONG)
    if previous["RROF_S"] <= previous["SIGNAL"] and current["RROF_S"] > current["SIGNAL"]:
        print()
        print("🟢 CROSS UP → LONG")
        return "LONG", current

    # CROSS DOWN (SHORT)
    if previous["RROF_S"] >= previous["SIGNAL"] and current["RROF_S"] < current["SIGNAL"]:
        print()
        print("🔴 CROSS DOWN → SHORT")
        return "SHORT", current

    print()
    print("🚫 NO NEW SIGNAL")
    return None, None

# ============================================================
# BUILD TELEGRAM MESSAGE
# ============================================================

def build_message(signal, current, symbol):
    direction = "🟢 LONG" if signal == "LONG" else "🔴 SHORT"
    cross = "RROF Smooth CROSS UP Signal" if signal == "LONG" else "RROF Smooth CROSS DOWN Signal"

    message = (
        f"{direction} <b>{symbol}</b>\n\n"
        #f"📊 Source: OKX\n"
        f"⏱ Timeframe: {TIMEFRAME}\n\n"
        f"💰 Price: {current['close']:.2f}\n"
        #f"📊 Volume: {current['volume']:,.4f}\n\n"
       # f"RROF: {current['RROF']:.2f}\n"
       # f"RROF Smooth: {current['RROF_S']:.2f}\n"
       # f"Signal: {current['SIGNAL']:.2f}\n\n"
       # f"🕐 Candle:\n{current['timestamp']}\n\n"
        #f"🔔 {cross}"
    )

    return message

# ============================================================
# MAIN
# ============================================================

def main():
    print()
    print("🚀 GOLD RROF OKX SCANNER")
    print("==========================================")
    print(f"Timeframe : {TIMEFRAME}")
    print(f"Candles   : {CANDLE_LIMIT}")
    print("==========================================")

    check_telegram_config()

    # Tìm symbol XAU/USD trên OKX
    global SYMBOL
    found_symbol, inst_type = find_xau_symbol()
    
    if found_symbol:
        SYMBOL = found_symbol
        print(f"✅ Sử dụng symbol: {SYMBOL} ({inst_type})")
    else:
        print("❌ Không tìm thấy symbol XAU/USD trên OKX")
        sys.exit(1)

    # LOAD DATA
    try:
        df = get_okx_candles(SYMBOL, inst_type)
    except Exception as e:
        print()
        print(f"❌ DATA ERROR: {e}")
        sys.exit(1)

    if df is None:
        print("❌ Không lấy được dữ liệu")
        sys.exit(1)

    # CALCULATE EVEREX
    print()
    print("🧮 Calculating EVEREX / RROF...")
    try:
        df = calculate_everex(df)
    except Exception as e:
        print()
        print(f"❌ EVEREX ERROR: {e}")
        sys.exit(1)

    # VALIDATION
    valid = df[["RROF", "RROF_S", "SIGNAL"]].dropna()
    print(f"✅ Valid RROF rows: {len(valid)}")
    if len(valid) < 20:
        print("❌ Không đủ dữ liệu để tính RROF")
        sys.exit(1)

    # CHECK SIGNAL
    signal, current = check_signal(df)

    # SEND TELEGRAM
    if signal in ["LONG", "SHORT"]:
        message = build_message(signal, current, SYMBOL)
        print()
        print("📨 Sending Telegram...")
        send_telegram(message)
    else:
        print()
        print("ℹ️ Không gửi Telegram vì không có tín hiệu mới.")

    print()
    print("✅ Scanner completed.")

# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
