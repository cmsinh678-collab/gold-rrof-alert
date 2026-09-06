import os
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime

# =========================================================
# CẤU HÌNH
# =========================================================
TIMEFRAME = "15m"
CANDLE_LIMIT = 200

# =========================================================
# CÀI ĐẶT EVEREX (THEO ẢNH CỦA BẠN)
# =========================================================
RROF_LENGTH = 10
RROF_MA_TYPE = "WMA"
SMOOTH = 3
SIGNAL_LENGTH = 5
SIGNAL_MA_TYPE = "WMA"
LOOKBACK = 20
LOOKBACK_MA_TYPE = "SMA"

# =========================================================
# TELEGRAM
# =========================================================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# =========================================================
# LẤY DỮ LIỆU TỪ OKX (DÙNG CHO XAUUSD)
# =========================================================
def get_okx_klines():
    """Lấy dữ liệu nến XAUUSD từ OKX Swap"""
    inst_id = "XAUUSD-SWAP"

    # Map khung thời gian sang định dạng của OKX
    interval_map = {
        '1m': '1m', '3m': '3m', '5m': '5m', '15m': '15m', '30m': '30m',
        '1h': '1H', '2h': '2H', '4h': '4H', '6h': '6H', '12h': '12H',
        '1d': '1D', '1w': '1W', '1M': '1M'
    }
    okx_interval = interval_map.get(TIMEFRAME, '15m')

    url = "https://www.okx.com/api/v5/market/history-candles"
    params = {
        'instId': inst_id,
        'bar': okx_interval,
        'limit': CANDLE_LIMIT
    }

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }

    try:
        print(f"🔄 Đang gọi OKX API cho {inst_id}...")
        r = requests.get(url, params=params, headers=headers, timeout=20)
        r.raise_for_status()

        data = r.json()
        if data['code'] != '0':
            raise Exception(f"OKX Error: {data.get('msg', 'Unknown error')}")

        candles = data['data']
        if not candles:
            raise Exception("Không có dữ liệu nến trả về")

        # Tạo DataFrame
        df = pd.DataFrame(candles, columns=[
            'timestamp', 'open', 'high', 'low', 'close', 'volCcy', 'vol', 'volCcyQuote', 'confirm'
        ])

        # Chuyển đổi kiểu dữ liệu
        for col in ['open', 'high', 'low', 'close', 'volCcy']:
            df[col] = df[col].astype(float)

        # OKX trả dữ liệu mới nhất trước, cần đảo ngược để cũ → mới
        df = df.iloc[::-1].reset_index(drop=True)
        df['timestamp'] = pd.to_datetime(df['timestamp'].astype(float), unit='ms')

        # Chỉ giữ các cột cần thiết
        df = df[['timestamp', 'open', 'high', 'low', 'close', 'volCcy']]
        df = df.rename(columns={'timestamp': 'time', 'volCcy': 'volume'})

        print(f"✅ Lấy thành công {len(df)} cây nến XAUUSD từ OKX.")
        return df

    except Exception as e:
        print(f"❌ Lỗi khi lấy dữ liệu từ OKX: {e}")
        raise

# =========================================================
# HÀM TÍNH TRUNG BÌNH ĐỘNG
# =========================================================
def get_average(series, length, ma_type):
    if ma_type == "SMA":
        return series.rolling(length).mean()
    elif ma_type == "EMA":
        return series.ewm(span=length, adjust=False).mean()
    elif ma_type == "RMA":
        return series.ewm(alpha=1 / length, adjust=False).mean()
    elif ma_type == "WMA":
        weights = np.arange(1, length + 1)
        return series.rolling(length).apply(
            lambda x: np.dot(x, weights) / weights.sum(), raw=True
        )
    else:
        return series.rolling(length).mean()

# =========================================================
# HÀM NORMALIZE CỦA EVEREX
# =========================================================
def normalize(value, avg):
    if avg is None or np.isnan(avg) or avg == 0:
        return 0.10
    x = value / avg
    return np.select(
        [x > 1.50, x > 1.20, x > 1.00, x > 0.80, x > 0.60, x > 0.40, x > 0.20],
        [1.00, 0.90, 0.80, 0.70, 0.60, 0.50, 0.25],
        default=0.10
    )

# =========================================================
# TÍNH EVEREX
# =========================================================
def calculate_everex(df):
    open_ = df["open"]
    high = df["high"]
    low = df["low"]
    close = df["close"]
    volume = df["volume"]

    # --- VOLUME ---
    vola = get_average(volume, LOOKBACK, LOOKBACK_MA_TYPE)
    vola_n = normalize(volume, vola) * 100

    # --- PRICE ---
    bar_spread = close - open_
    bar_range = high - low
    bar_range = bar_range.replace(0, np.nan)

    r2 = high.rolling(2).max() - low.rolling(2).min()
    r2 = r2.replace(0, np.nan)

    src_shift = close.diff()
    sign_shift = np.sign(src_shift)
    sign_spread = np.sign(bar_spread)

    barclosing = (2 * (close - low) / bar_range * 100) - 100
    s2r = bar_spread / bar_range * 100

    bar_spread_abs = abs(bar_spread)
    bar_spread_avg = get_average(bar_spread_abs, LOOKBACK, LOOKBACK_MA_TYPE)
    bar_spread_ratio_n = normalize(bar_spread_abs, bar_spread_avg) * 100 * sign_spread

    barclosing_2 = (2 * (close - low.rolling(2).min()) / r2 * 100) - 100
    shift2bar_to_r2 = src_shift / r2 * 100

    src_shift_abs = abs(src_shift)
    srcshift_avg = get_average(src_shift_abs, LOOKBACK, LOOKBACK_MA_TYPE)
    srcshift_ratio_n = normalize(src_shift_abs, srcshift_avg) * 100 * sign_shift

    pricea_n = (
        barclosing + s2r + bar_spread_ratio_n +
        barclosing_2 + shift2bar_to_r2 + srcshift_ratio_n
    ) / 6

    bar_flow = pricea_n * vola_n / 100

    bulls = bar_flow.clip(lower=0)
    bears = (-bar_flow.clip(upper=0))

    bulls_avg = get_average(bulls, RROF_LENGTH, RROF_MA_TYPE)
    bears_avg = get_average(bears, RROF_LENGTH, RROF_MA_TYPE)

    dx = bulls_avg / bears_avg
    rrof = 2 * (100 - 100 / (1 + dx)) - 100

    rrof_s = get_average(rrof, SMOOTH, "WMA")
    signal = get_average(rrof_s, SIGNAL_LENGTH, SIGNAL_MA_TYPE)

    df["RROF"] = rrof
    df["RROF_S"] = rrof_s
    df["SIGNAL"] = signal

    return df

# =========================================================
# GỬI TIN NHẮN TELEGRAM
# =========================================================
def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("⚠️ Chưa cấu hình Telegram Token hoặc Chat ID")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(
            url,
            json={"chat_id": CHAT_ID, "text": message},
            timeout=20
        )
        print(r.text)
    except Exception as e:
        print(f"❌ Lỗi gửi Telegram: {e}")

# =========================================================
# KIỂM TRA TÍN HIỆU
# =========================================================
def check_signal(df):
    if len(df) < 5:
        print("⚠️ Không đủ dữ liệu để kiểm tra tín hiệu")
        return

    previous = df.iloc[-3]
    current = df.iloc[-2]

    prev_rrof = previous["RROF_S"]
    prev_signal = previous["SIGNAL"]
    curr_rrof = current["RROF_S"]
    curr_signal = current["SIGNAL"]

    print(f"\n⏱ TIMEFRAME: {TIMEFRAME}")
    print(f"📊 Previous: RROF_S={prev_rrof:.4f}, SIGNAL={prev_signal:.4f}")
    print(f"📊 Current:  RROF_S={curr_rrof:.4f}, SIGNAL={curr_signal:.4f}")

    if prev_rrof <= prev_signal and curr_rrof > curr_signal:
        message = f"""
🟢 XAUUSD LONG
📊 RROF Smooth crossed ABOVE Signal
⏱ Timeframe: {TIMEFRAME}
💰 Price: {current['close']:.2f}
🕐 Candle: {current['time']}
"""
        send_telegram(message)
        print("✅ Đã gửi tín hiệu LONG")

    elif prev_rrof >= prev_signal and curr_rrof < curr_signal:
        message = f"""
🔴 XAUUSD SHORT
📊 RROF Smooth crossed BELOW Signal
⏱ Timeframe: {TIMEFRAME}
💰 Price: {current['close']:.2f}
🕐 Candle: {current['time']}
"""
        send_telegram(message)
        print("✅ Đã gửi tín hiệu SHORT")

    else:
        print("🚫 Không có tín hiệu.")

# =========================================================
# HÀM CHÍNH
# =========================================================
def main():
    print("🚀 Bắt đầu quét tín hiệu XAUUSD...")
    try:
        df = get_okx_klines()
        df = calculate_everex(df)
        df = df.dropna()
        check_signal(df)
    except Exception as e:
        print(f"❌ Lỗi trong quá trình quét: {e}")
        raise

if __name__ == "__main__":
    main()
