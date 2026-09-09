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

# Danh sách các symbol cần quét
SYMBOLS = ["XAU-USDT", "ETH-USDT"]

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

def find_symbol(search_term):
    """Tìm symbol trên OKX (ưu tiên Swap, sau đó Spot)"""
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

    numeric_columns = ["open", "high", "low", "close", "volume", "volume_currency", "volume_quote"]
    for column in numeric_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df["timestamp"] = pd.to_datetime(pd.to_numeric(df["timestamp"], errors="coerce"), unit="ms", utc=True)

    df = df.sort_values("timestamp")
    df = df.drop_duplicates(subset=["timestamp"])
    df = df.reset_index(drop=True)

    df = df.dropna(subset=["timestamp", "open", "high", "low", "close", "volume"])

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

    print()
    print("=" * 70)
    print("🕯 CANDLE STATUS")
    print("=" * 70)
    print(f"Latest confirm: {df.iloc[-1]['confirm']}")
    print("0 = chưa đóng, 1 = đã đóng")

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
# SIGNAL CHECK
# ============================================================

def check_signal(df, symbol_name):
    """Kiểm tra tín hiệu và trả về dict kết quả"""
    result = {
        'symbol': symbol_name,
        'signal': None,
        'price': None,
        'timestamp': None,
        'rrof': None,
        'signal_line': None,
        'volume': None
    }
    
    if len(df) < 20:
        print(f"⚠️ {symbol_name}: Không đủ dữ liệu")
        return result

    confirmed = df[df["confirm"].astype(str) == "1"].copy()

    if len(confirmed) < 2:
        print(f"⚠️ {symbol_name}: Không tìm đủ 2 nến đã đóng")
        return result

    previous = confirmed.iloc[-2]
    current = confirmed.iloc[-1]

    result['price'] = float(current['close'])
    result['timestamp'] = current['timestamp']
    result['rrof'] = float(current['RROF'])
    result['signal_line'] = float(current['SIGNAL'])
    result['volume'] = float(current['volume'])

    if previous["RROF_S"] <= previous["SIGNAL"] and current["RROF_S"] > current["SIGNAL"]:
        print(f"🟢 {symbol_name}: CROSS UP → LONG")
        result['signal'] = 'LONG'
        return result

    if previous["RROF_S"] >= previous["SIGNAL"] and current["RROF_S"] < current["SIGNAL"]:
        print(f"🔴 {symbol_name}: CROSS DOWN → SHORT")
        result['signal'] = 'SHORT'
        return result

    print(f"🚫 {symbol_name}: NO SIGNAL")
    return result

# ============================================================
# BUILD TELEGRAM MESSAGE - ĐƠN GIẢN NHẤT
# ============================================================

def build_message(results):
    """Gửi tin nhắn đơn giản: long/short symbol price signal rrof"""
    signals = [r for r in results if r['signal'] is not None]
    
    if not signals:
        return None
    
    lines = []
    for s in signals:
        # Định dạng: long xau price signal rrof
        line = f"{s['signal'].lower()} {s['symbol']} {s['price']:.2f} {s['signal_line']:.2f} {s['rrof']:.2f}"
        lines.append(line)
    
    return "\n".join(lines)

# ============================================================
# MAIN
# ============================================================

def main():
    print()
    print("🚀 MULTI-COIN RROF OKX SCANNER")
    print("==========================================")
    print(f"Timeframe : {TIMEFRAME}")
    print(f"Candles   : {CANDLE_LIMIT}")
    print(f"Symbols   : {SYMBOLS}")
    print("==========================================")

    check_telegram_config()

    results = []
    
    for search_term in SYMBOLS:
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
            print(f"❌ Không lấy được dữ liệu cho {search_term}")
            continue

        print("🧮 Calculating EVEREX / RROF...")
        try:
            df = calculate_everex(df)
        except Exception as e:
            print(f"❌ EVEREX ERROR: {e}")
            continue

        result = check_signal(df, search_term)
        results.append(result)

    message = build_message(results)
    
    if message:
        print()
        print("📨 Sending Telegram...")
        send_telegram(message)
    else:
        print()
        print("ℹ️ Không có tín hiệu mới. Không gửi Telegram.")

    print()
    print("✅ Scanner completed.")

# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
