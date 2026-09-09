import os
import sys
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime
import json

# ============================================================
# CONFIG
# ============================================================

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

# File lưu trạng thái tín hiệu đã báo
STATE_FILE = "signal_state.json"

# ============================================================
# STATE MANAGEMENT
# ============================================================

def load_state():
    """Đọc trạng thái tín hiệu đã báo từ file"""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_state(state):
    """Lưu trạng thái tín hiệu đã báo vào file"""
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f)

def is_signal_reported(symbol, signal_type, timestamp):
    """Kiểm tra tín hiệu đã được báo chưa"""
    state = load_state()
    key = f"{symbol}_{signal_type}_{timestamp}"
    return state.get(key, False)

def mark_signal_reported(symbol, signal_type, timestamp):
    """Đánh dấu tín hiệu đã được báo"""
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
# GET OKX CANDLES
# ============================================================

def get_okx_candles(symbol, inst_type="SPOT"):
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

    print(f"✅ Lấy thành công {len(df)} candles")
    print(f"💰 Last price: {df.iloc[-1]['close']:.2f}")
    print(f"🕯 Nến cuối confirm: {df.iloc[-1]['confirm']} (0=đang mở, 1=đã đóng)")

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
# SIGNAL CHECK - PHÁT HIỆN CẮT GIỮA TD2 VÀ TD1
# ============================================================

def check_signal(df, symbol_name):
    """
    Phát hiện tín hiệu cắt giữa 2 nến đã đóng gần nhất:
    - TD2: nến -2 (đã đóng)
    - TD1: nến -1 (đã đóng)
    Bỏ qua nến HT (nến 0 đang mở)
    """
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

    # Lấy tất cả nến đã đóng
    confirmed = df[df["confirm"].astype(str) == "1"].copy()

    if len(confirmed) < 2:
        print(f"⚠️ {symbol_name}: Không tìm đủ 2 nến đã đóng")
        return result

    # Lấy 2 nến đã đóng gần nhất: TD1 và TD2
    td1 = confirmed.iloc[-1]   # nến -1 (vừa đóng)
    td2 = confirmed.iloc[-2]   # nến -2 (đóng trước đó)

    # In thông tin debug
    print(f"📊 TD2 (nến -2): {td2['timestamp']} | RROF_S={td2['RROF_S']:.2f}, SIGNAL={td2['SIGNAL']:.2f}")
    print(f"📊 TD1 (nến -1): {td1['timestamp']} | RROF_S={td1['RROF_S']:.2f}, SIGNAL={td1['SIGNAL']:.2f}")
    
    # Lấy thông tin nến HT (đang mở)
    ht = df.iloc[-1]
    print(f"🕯 Nến HT (đang mở): {ht['timestamp']} | confirm={ht['confirm']}")

    # Lưu thông tin kết quả (lấy từ TD1 - nến vừa đóng)
    result['price'] = float(td1['close'])
    result['timestamp'] = td1['timestamp']
    result['rrof'] = float(td1['RROF'])
    result['signal_line'] = float(td1['SIGNAL'])
    result['volume'] = float(td1['volume'])
    result['signal_timestamp'] = td1['timestamp']

    # --- KIỂM TRA CẮT GIỮA TD2 VÀ TD1 ---
    # LONG: TD2 <= Signal, TD1 > Signal
    if td2["RROF_S"] <= td2["SIGNAL"] and td1["RROF_S"] > td1["SIGNAL"]:
        timestamp_key = td1['timestamp'].strftime('%Y%m%d%H%M')
        if not is_signal_reported(symbol_name, 'LONG', timestamp_key):
            print(f"🟢 {symbol_name}: CẮT LÊN (LONG) tại nến TD1 {td1['timestamp']}")
            result['signal'] = 'LONG'
            mark_signal_reported(symbol_name, 'LONG', timestamp_key)
            return result
        else:
            print(f"ℹ️ {symbol_name}: Tín hiệu LONG đã báo trước đó")
            return result

    # SHORT: TD2 >= Signal, TD1 < Signal
    if td2["RROF_S"] >= td2["SIGNAL"] and td1["RROF_S"] < td1["SIGNAL"]:
        timestamp_key = td1['timestamp'].strftime('%Y%m%d%H%M')
        if not is_signal_reported(symbol_name, 'SHORT', timestamp_key):
            print(f"🔴 {symbol_name}: CẮT XUỐNG (SHORT) tại nến TD1 {td1['timestamp']}")
            result['signal'] = 'SHORT'
            mark_signal_reported(symbol_name, 'SHORT', timestamp_key)
            return result
        else:
            print(f"ℹ️ {symbol_name}: Tín hiệu SHORT đã báo trước đó")
            return result

    print(f"🚫 {symbol_name}: Không có cắt giữa TD2 và TD1")
    return result

# ============================================================
# BUILD TELEGRAM MESSAGE - ĐƠN GIẢN
# ============================================================

def build_message(results):
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
    print(f"Symbols   : {SYMBOLS}")
    print("==========================================")

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
