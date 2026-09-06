import os
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# =========================================================
# CÀI ĐẶT THƯ VIỆN pricehub (nếu chưa có)
# =========================================================
try:
    from pricehub import get_ohlc
except ImportError:
    print("⚠️ Thư viện pricehub chưa được cài đặt. Đang cài đặt...")
    os.system("pip install pricehub")
    from pricehub import get_ohlc

# =========================================================
# CONFIG
# =========================================================
SYMBOL = "XAUUSD"
TIMEFRAME = "15min"  # Hoặc "15m" nếu pricehub yêu cầu
CANDLE_LIMIT = 200

# =========================================================
# EVEREX SETTINGS (THEO ẢNH CỦA BẠN)
# =========================================================
RROF_LENGTH = 10
RROF_MA_TYPE = "WMA"
SMOOTH = 3
SIGNAL_LENGTH = 5
SIGNAL_MA_TYPE = "WMA"
LOOKBACK = 20          # Đúng với ảnh của bạn
LOOKBACK_MA_TYPE = "SMA"  # Đúng với ảnh của bạn

# =========================================================
# TELEGRAM (vẫn giữ nguyên)
# =========================================================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# =========================================================
# CÁC HÀM TRỢ GIÚP
# =========================================================
def get_average(series, length, ma_type):
    """Tính trung bình động"""
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

def normalize(value, avg):
    """Hàm normalize của chỉ báo EVEREX"""
    if avg is None or np.isnan(avg) or avg == 0:
        return 0.10
    x = value / avg
    return np.select(
        [
            x > 1.50, x > 1.20, x > 1.00,
            x > 0.80, x > 0.60, x > 0.40, x > 0.20
        ],
        [1.00, 0.90, 0.80, 0.70, 0.60, 0.50, 0.25],
        default=0.10
    )

# =========================================================
# GET DATA (SỬ DỤNG OKX QUA pricehub)
# =========================================================
def get_gold_data():
    """Lấy dữ liệu XAU/USD từ OKX thông qua pricehub"""
    try:
        end = datetime.now()
        start = end - timedelta(days=7)  # Lấy 7 ngày dữ liệu

        print(f"🔄 Đang lấy dữ liệu XAU/USD từ OKX (khung {TIMEFRAME})...")

        # Lấy dữ liệu từ pricehub
        df = get_ohlc(
            broker="okx_spot",        # Hoặc "okx_futures" nếu cần
            symbol="XAU-USDT",        # Cặp giao dịch trên OKX
            interval=TIMEFRAME,
            start=start,
            end=end
        )

        if df is None or len(df) == 0:
            raise Exception("Không lấy được dữ liệu từ OKX")

        # pricehub trả về DataFrame với các cột: timestamp, open, high, low, close, volume
        # Đổi tên cột để khớp với code cũ
        df = df.rename(columns={'timestamp': 'time'})

        # Sắp xếp theo thời gian tăng dần (cũ → mới)
        df = df.sort_values('time').reset_index(drop=True)

        print(f"✅ Lấy thành công {len(df)} cây nến từ OKX.")
        return df

    except Exception as e:
        print(f"❌ Lỗi khi lấy dữ liệu từ OKX: {e}")
        raise

# =========================================================
# EVEREX CALCULATION (ĐÃ SỬA THEO ẢNH CỦA BẠN)
# =========================================================
def calculate_everex(df):
    """Tính toán chỉ báo EVEREX"""
    open_ = df["open"]
    high = df["high"]
    low = df["low"]
    close = df["close"]
    volume = df["volume"]

    # --- VOLUME: Dùng SMA cho LOOKBACK ---
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

    # 1. Bar Closing
    barclosing = (2 * (close - low) / bar_range * 100) - 100

    # 2. Spread to Range
    s2r = bar_spread / bar_range * 100

    # 3. Bar Spread Ratio Normalized
    bar_spread_abs = abs(bar_spread)
    bar_spread_avg = get_average(bar_spread_abs, LOOKBACK, LOOKBACK_MA_TYPE)
    bar_spread_ratio_n = normalize(bar_spread_abs, bar_spread_avg) * 100 * sign_spread

    # 4. Bar Closing 2
    barclosing_2 = (2 * (close - low.rolling(2).min()) / r2 * 100) - 100

    # 5. Shift 2 Bar to R2
    shift2bar_to_r2 = src_shift / r2 * 100

    # 6. Shift Ratio Normalized
    src_shift_abs = abs(src_shift)
    srcshift_avg = get_average(src_shift_abs, LOOKBACK, LOOKBACK_MA_TYPE)
    srcshift_ratio_n = normalize(src_shift_abs, srcshift_avg) * 100 * sign_shift

    # --- PRICE NORMALIZED ---
    pricea_n = (
        barclosing + s2r + bar_spread_ratio_n +
        barclosing_2 + shift2bar_to_r2 + srcshift_ratio_n
    ) / 6

    # --- BAR FLOW ---
    bar_flow = pricea_n * vola_n / 100

    # --- BULLS / BEARS ---
    bulls = bar_flow.clip(lower=0)
    bears = (-bar_flow.clip(upper=0))

    # Bulls / Bears Average với WMA (RROF)
    bulls_avg = get_average(bulls, RROF_LENGTH, RROF_MA_TYPE)
    bears_avg = get_average(bears, RROF_LENGTH, RROF_MA_TYPE)

    # --- RROF ---
    dx = bulls_avg / bears_avg
    rrof = 2 * (100 - 100 / (1 + dx)) - 100

    # --- RROF SMOOTH & SIGNAL ---
    rrof_s = get_average(rrof, SMOOTH, "WMA")
    signal = get_average(rrof_s, SIGNAL_LENGTH, SIGNAL_MA_TYPE)

    # --- GÁN VÀO DATAFRAME ---
    df["RROF"] = rrof
    df["RROF_S"] = rrof_s
    df["SIGNAL"] = signal

    return df

# =========================================================
# TELEGRAM
# =========================================================
def send_telegram(message):
    """Gửi tin nhắn đến Telegram"""
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
# CHECK SIGNAL
# =========================================================
def check_signal(df):
    """Kiểm tra tín hiệu cắt của RROF_S và Signal"""
    if len(df) < 5:
        print("⚠️ Không đủ dữ liệu để kiểm tra tín hiệu")
        return

    # Lấy 2 nến đã đóng gần nhất
    previous = df.iloc[-3]
    current = df.iloc[-2]

    prev_rrof = previous["RROF_S"]
    prev_signal = previous["SIGNAL"]
    curr_rrof = current["RROF_S"]
    curr_signal = current["SIGNAL"]

    print(f"\n⏱ TIMEFRAME: {TIMEFRAME}")
    print(f"📊 Previous: RROF_S={prev_rrof:.4f}, SIGNAL={prev_signal:.4f}")
    print(f"📊 Current:  RROF_S={curr_rrof:.4f}, SIGNAL={curr_signal:.4f}")

    # LONG: RROF cắt lên trên Signal
    if prev_rrof <= prev_signal and curr_rrof > curr_signal:
        message = f"""
🟢 XAUUSD LONG

📊 RROF Smooth crossed ABOVE Signal
⏱ Timeframe: {TIMEFRAME}
💰 Price: {current['close']}
🕐 Candle: {current['time']}
"""
        send_telegram(message)
        print("✅ Đã gửi tín hiệu LONG")

    # SHORT: RROF cắt xuống dưới Signal
    elif prev_rrof >= prev_signal and curr_rrof < curr_signal:
        message = f"""
🔴 XAUUSD SHORT

📊 RROF Smooth crossed BELOW Signal
⏱ Timeframe: {TIMEFRAME}
💰 Price: {current['close']}
🕐 Candle: {current['time']}
"""
        send_telegram(message)
        print("✅ Đã gửi tín hiệu SHORT")

    else:
        print("🚫 Không có tín hiệu.")

# =========================================================
# MAIN
# =========================================================
def main():
    print("🚀 Bắt đầu quét tín hiệu...")
    try:
        df = get_gold_data()
        df = calculate_everex(df)
        df = df.dropna()
        check_signal(df)
    except Exception as e:
        print(f"❌ Lỗi trong quá trình quét: {e}")
        raise

if __name__ == "__main__":
    main()
