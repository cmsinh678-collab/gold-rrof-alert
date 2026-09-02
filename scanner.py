import os
import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime

# =========================================================
# CONFIG
# =========================================================
SYMBOL = "XAUUSD"
TIMEFRAME = "15min"
CANDLE_LIMIT = 200

# =========================================================
# EVEREX SETTINGS
# =========================================================
RROF_LENGTH = 10
RROF_MA_TYPE = "WMA"
SMOOTH = 3
SIGNAL_LENGTH = 5
SIGNAL_MA_TYPE = "WMA"
LOOKBACK = 20

# =========================================================
# TELEGRAM
# =========================================================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# =========================================================
# MOVING AVERAGE
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
        return series.rolling(length).apply(lambda x: np.dot(x, weights) / weights.sum(), raw=True)
    else:
        return series.rolling(length).mean()

# =========================================================
# NORMALIZE
# =========================================================
def normalize(value, avg):
    x = value / avg
    return np.select(
        [x > 1.50, x > 1.20, x > 1.00, x > 0.80, x > 0.60, x > 0.40, x > 0.20],
        [1.00, 0.90, 0.80, 0.70, 0.60, 0.50, 0.25],
        default=0.10
    )

def get_gold_data():

    API_KEY = os.getenv("TWELVEDATA_API_KEY")

    if not API_KEY:
        raise Exception("❌ Không tìm thấy TWELVEDATA_API_KEY trong GitHub Secrets")

    url = "https://api.twelvedata.com/time_series"

    params = {
        "symbol": "XAU/USD",
        "interval": TIMEFRAME,
        "outputsize": CANDLE_LIMIT,

        # QUAN TRỌNG
        "apikey": API_KEY
    }

    for attempt in range(3):

        try:

            print(f"🔄 Đang gọi Twelve Data lần {attempt + 1}/3...")

            r = requests.get(
                url,
                params=params,
                timeout=60
            )

            # In URL nhưng KHÔNG in API key
            print("HTTP Status:", r.status_code)

            # Nếu lỗi thì in nội dung lỗi của Twelve Data
            if r.status_code != 200:
                print("❌ API Response:", r.text)

            r.raise_for_status()

            data = r.json()

            # Kiểm tra lỗi API
            if data.get("status") == "error":

                raise Exception(
                    f"Twelve Data Error: {data.get('message')}"
                )

            if "values" not in data:

                raise Exception(
                    f"Không có dữ liệu values: {data}"
                )

            values = data["values"]

            df = pd.DataFrame({
                "time": pd.to_datetime(
                    [d["datetime"] for d in values]
                ),

                "open": [
                    float(d["open"])
                    for d in values
                ],

                "high": [
                    float(d["high"])
                    for d in values
                ],

                "low": [
                    float(d["low"])
                    for d in values
                ],

                "close": [
                    float(d["close"])
                    for d in values
                ],

                "volume": [
                    float(d.get("volume", 0) or 0)
                    for d in values
                ]
            })

            # Twelve Data trả dữ liệu mới → cũ
            # Ta đổi thành cũ → mới
            df = df.sort_values(
                "time"
            ).reset_index(
                drop=True
            )

            print(
                f"✅ Lấy thành công {len(df)} cây nến."
            )

            return df


        except Exception as e:

            print(
                f"❌ Lỗi lần {attempt + 1}: {e}"
            )

            if attempt < 2:

                print(
                    "⏳ Chờ 5 giây..."
                )

                time.sleep(5)

            else:

                raise Exception(
                    "Không thể lấy dữ liệu sau 3 lần thử."
                )
# =========================================================
# EVEREX CALCULATION
# =========================================================
def calculate_everex(df):
    open_ = df["open"]
    high = df["high"]
    low = df["low"]
    close = df["close"]
    volume = df["volume"]

    # --- VOLUME ---
    # Dùng SMA cho LOOKBACK theo đúng cài đặt trên TradingView
    vola = get_average(volume, LOOKBACK, "SMA")
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

    # 1
    barclosing = (2 * (close - low) / bar_range * 100) - 100

    # 2
    s2r = bar_spread / bar_range * 100

    # 3
    bar_spread_abs = abs(bar_spread)
    # Dùng SMA cho LOOKBACK
    bar_spread_avg = get_average(bar_spread_abs, LOOKBACK, "SMA")
    bar_spread_ratio_n = normalize(bar_spread_abs, bar_spread_avg) * 100 * sign_spread

    # 4
    barclosing_2 = (2 * (close - low.rolling(2).min()) / r2 * 100) - 100

    # 5
    shift2bar_to_r2 = src_shift / r2 * 100

    # 6
    src_shift_abs = abs(src_shift)
    # Dùng SMA cho LOOKBACK
    srcshift_avg = get_average(src_shift_abs, LOOKBACK, "SMA")
    srcshift_ratio_n = normalize(src_shift_abs, srcshift_avg) * 100 * sign_shift

    # --- PRICE NORMALIZED ---
    pricea_n = (barclosing + s2r + bar_spread_ratio_n + barclosing_2 + shift2bar_to_r2 + srcshift_ratio_n) / 6

    # --- BAR FLOW ---
    bar_flow = pricea_n * vola_n / 100

    # --- BULLS / BEARS ---
    bulls = bar_flow.clip(lower=0)
    bears = (-bar_flow.clip(upper=0))

    # Vẫn dùng RROF_MA_TYPE (WMA) cho RROF và Signal
    bulls_avg = get_average(bulls, RROF_LENGTH, RROF_MA_TYPE)
    bears_avg = get_average(bears, RROF_LENGTH, RROF_MA_TYPE)

    # --- RROF ---
    dx = bulls_avg / bears_avg
    rrof = 2 * (100 - 100 / (1 + dx)) - 100

    # RROF SMOOTH & SIGNAL (giữ nguyên WMA)
    rrof_s = get_average(rrof, SMOOTH, "WMA")
    signal = get_average(rrof_s, SIGNAL_LENGTH, SIGNAL_MA_TYPE)

    df["RROF"] = rrof
    df["RROF_S"] = rrof_s
    df["SIGNAL"] = signal

    return df
# =========================================================
# TELEGRAM
# =========================================================
def send_telegram(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": CHAT_ID, "text": message}, timeout=20)
    print(r.text)

# =========================================================
# CHECK SIGNAL
# =========================================================
def check_signal(df):
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
💰 Price: {current['close']}
🕐 Candle: {current['time']}
"""
        send_telegram(message)

    elif prev_rrof >= prev_signal and curr_rrof < curr_signal:
        message = f"""
🔴 XAUUSD SHORT
📊 RROF Smooth crossed BELOW Signal
⏱ Timeframe: {TIMEFRAME}
💰 Price: {current['close']}
🕐 Candle: {current['time']}
"""
        send_telegram(message)

    else:
        print("🚫 No signal.")

# =========================================================
# MAIN
# =========================================================
def main():
    print("🚀 Bắt đầu quét tín hiệu...")
    df = get_gold_data()
    df = calculate_everex(df)
    df = df.dropna()
    check_signal(df)

if __name__ == "__main__":
    main()
