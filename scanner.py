import os
import sys
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

# ============================================================
# CONFIG
# ============================================================

SYMBOL = "XAUUSDT"

# Binance interval: 30m
TIMEFRAME = "30m"

# Số nến lấy về
CANDLE_LIMIT = 500

# EVEREX
RROF_LENGTH = 10
RROF_MA_TYPE = "WMA"

SMOOTH = 3

SIGNAL_LENGTH = 5
SIGNAL_MA_TYPE = "WMA"

LOOKBACK = 20

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ============================================================
# CHECK ENV
# ============================================================

if not TELEGRAM_BOT_TOKEN:
    print("❌ TELEGRAM_BOT_TOKEN chưa được thiết lập")
    sys.exit(1)

if not TELEGRAM_CHAT_ID:
    print("❌ TELEGRAM_CHAT_ID chưa được thiết lập")
    sys.exit(1)


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    data = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML"
    }

    try:
        r = requests.post(url, data=data, timeout=20)

        print("Telegram:", r.status_code)

        if r.status_code != 200:
            print(r.text)

        return r.status_code == 200

    except Exception as e:

        print("❌ Telegram error:", e)

        return False


# ============================================================
# WMA
# ============================================================

def wma(series, length):

    weights = np.arange(1, length + 1)

    return series.rolling(length).apply(
        lambda x: np.dot(x, weights) / weights.sum(),
        raw=True
    )


# ============================================================
# GENERAL MA
# ============================================================

def get_average(series, length, ma_type):

    ma_type = ma_type.upper()

    if ma_type == "SMA":

        return series.rolling(length).mean()

    elif ma_type == "EMA":

        return series.ewm(
            span=length,
            adjust=False
        ).mean()

    elif ma_type == "RMA":

        return series.ewm(
            alpha=1 / length,
            adjust=False
        ).mean()

    elif ma_type == "WMA":

        return wma(series, length)

    else:

        raise ValueError(
            f"Unsupported MA type: {ma_type}"
        )


# ============================================================
# EVEREX NORMALIZE
# ============================================================

def normalize(value, average):

    x = value / average.replace(0, np.nan)

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
        [
            1.00,
            0.90,
            0.80,
            0.70,
            0.60,
            0.50,
            0.25
        ],
        default=0.10
    )

    return pd.Series(
        result,
        index=value.index
    )


# ============================================================
# DOWNLOAD BINANCE DATA
# ============================================================

def download_binance():

    print()
    print("=" * 70)
    print("📥 BINANCE FUTURES XAUUSDT")
    print("=" * 70)

    KLINE_URL = "https://fapi.binance.com/fapi/v1/klines"
    TICKER_URL = "https://fapi.binance.com/fapi/v1/ticker/price"

    # ============================================================
    # QUAN TRỌNG: Thêm headers để tránh bị chặn 451
    # ============================================================
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
    }

    params = {
        "symbol": SYMBOL,
        "interval": TIMEFRAME,
        "limit": CANDLE_LIMIT
    }

    print(f"📊 Symbol : {SYMBOL}")
    print(f"⏱ TF     : {TIMEFRAME}")
    print(f"📈 Limit  : {CANDLE_LIMIT}")

    try:

        # ====================================================
        # 1. CURRENT PRICE
        # ====================================================

        ticker = requests.get(
            TICKER_URL,
            params={"symbol": SYMBOL},
            headers=headers,
            timeout=15
        )

        ticker.raise_for_status()

        ticker_data = ticker.json()

        live_price = float(ticker_data["price"])

        print()
        print(f"💰 Binance LIVE PRICE : {live_price:.2f}")

        # ====================================================
        # 2. KLINES
        # ====================================================

        r = requests.get(
            KLINE_URL,
            params=params,
            headers=headers,
            timeout=15
        )

        r.raise_for_status()

        data = r.json()

        if not data:
            print("❌ Binance không trả về KLINE")
            return None

    except requests.exceptions.RequestException as e:

        print(f"❌ Binance API error: {e}")

        return None

    except Exception as e:

        print(f"❌ Binance data error: {e}")

        return None

    # ============================================================
    # DATAFRAME
    # ============================================================

    columns = [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "trades",
        "taker_buy_volume",
        "taker_buy_quote_volume",
        "ignore"
    ]

    df = pd.DataFrame(
        data,
        columns=columns
    )

    # ============================================================
    # NUMERIC
    # ============================================================

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "trades",
        "taker_buy_volume",
        "taker_buy_quote_volume"
    ]

    for col in numeric_columns:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    # ============================================================
    # TIMESTAMP
    # ============================================================

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        unit="ms",
        utc=True
    )

    df["close_time"] = pd.to_datetime(
        df["close_time"],
        unit="ms",
        utc=True
    )

    df = (
        df
        .sort_values("timestamp")
        .drop_duplicates("timestamp")
        .reset_index(drop=True)
    )

    # ============================================================
    # VALIDATION
    # ============================================================

    if df.empty:

        print("❌ DataFrame rỗng")

        return None

    if df[
        ["open", "high", "low", "close", "volume"]
    ].isna().any().any():

        print("❌ OHLCV chứa NaN")

        return None

    if (df["volume"] < 0).any():

        print("❌ Volume không hợp lệ")

        return None

    # ============================================================
    # DATA INFO
    # ============================================================

    print()
    print("=" * 70)
    print("📊 BINANCE DATA INFO")
    print("=" * 70)

    print(f"✅ Candles : {len(df)}")

    print(
        f"📅 From    : {df['timestamp'].iloc[0]}"
    )

    print(
        f"📅 To      : {df['timestamp'].iloc[-1]}"
    )

    print()
    print("📋 LAST 5 CANDLES")

    print(
        df.tail(5)[[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]].to_string(index=False)
    )

    # ============================================================
    # LIVE VS CANDLE
    # ============================================================

    last_candle = df.iloc[-1]

    print()
    print("=" * 70)
    print("💰 PRICE CHECK")
    print("=" * 70)

    print(
        f"Binance LIVE PRICE : {live_price:.2f}"
    )

    print(
        f"Last KLINE CLOSE   : {last_candle['close']:.2f}"
    )

    print(
        f"Previous KLINE     : {df.iloc[-2]['close']:.2f}"
    )

    print(
        f"Difference LIVE/KLINE : "
        f"{live_price - last_candle['close']:+.2f}"
    )

    print()
    print(
        f"Last candle time : {last_candle['timestamp']}"
    )

    print(
        f"Last candle close: {last_candle['close']:.2f}"
    )

    print(
        f"Last candle vol  : {last_candle['volume']:,.2f}"
    )

    # ============================================================
    # VOLUME CHECK
    # ============================================================

    print()
    print("=" * 70)
    print("🔊 VOLUME STATISTICS")
    print("=" * 70)

    print(f"Volume min  : {df['volume'].min():,.2f}")
    print(f"Volume max  : {df['volume'].max():,.2f}")
    print(f"Volume avg  : {df['volume'].mean():,.2f}")
    print(f"Volume zero : {(df['volume'] == 0).sum()}")

    # ============================================================
    # IMPORTANT
    # ============================================================

    # Không dùng live price để tính EVEREX.
    #
    # EVEREX phải dùng OHLCV của các cây nến.
    #
    # [-1] = cây M30 hiện tại
    # [-2] = cây M30 đã đóng

    return df


# ============================================================
# LOAD DATA
# ============================================================

def load_data():

    df = download_binance()

    if df is None:
        return None

    # Chỉ lấy CANDLE_LIMIT nến cuối
    df = df.tail(CANDLE_LIMIT).copy()

    print()
    print(f"✅ Using last {len(df)} candles")

    return df.reset_index(drop=True)


# ============================================================
# EVEREX
# ============================================================

def calculate_everex(df):

    df = df.copy()

    volume = df["volume"]

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    vola = get_average(
        volume,
        LOOKBACK,
        "SMA"
    )

    vola_n = (
        normalize(
            volume,
            vola
        ) * 100
    )

    # --------------------------------------------------------
    # PRICE
    # --------------------------------------------------------

    bar_spread = (
        df["close"] -
        df["open"]
    )

    bar_range = (
        df["high"] -
        df["low"]
    )

    bar_range = bar_range.replace(
        0,
        np.nan
    )

    r2 = (
        df["high"].rolling(2).max()
        -
        df["low"].rolling(2).min()
    )

    r2 = r2.replace(
        0,
        np.nan
    )

    src_shift = df["close"].diff()

    # --------------------------------------------------------
    # SIGNS
    # --------------------------------------------------------

    sign_spread = np.where(
        bar_spread >= 0,
        1,
        -1
    )

    sign_shift = np.where(
        src_shift >= 0,
        1,
        -1
    )

    # --------------------------------------------------------
    # BAR CLOSING
    # --------------------------------------------------------

    barclosing = (
        2
        *
        (
            (
                df["close"] -
                df["low"]
            )
            /
            bar_range
        )
        * 100
    ) - 100

    # --------------------------------------------------------
    # SPREAD / RANGE
    # --------------------------------------------------------

    s2r = (
        bar_spread /
        bar_range
    ) * 100

    # --------------------------------------------------------
    # SPREAD RATIO
    # --------------------------------------------------------

    spread_avg = get_average(
        abs(bar_spread),
        LOOKBACK,
        "SMA"
    )

    bar_spread_ratio_n = (
        normalize(
            abs(bar_spread),
            spread_avg
        )
        * 100
        * sign_spread
    )

    # --------------------------------------------------------
    # 2 BAR CLOSING
    # --------------------------------------------------------

    low2 = (
        df["low"]
        .rolling(2)
        .min()
    )

    barclosing_2 = (
        2
        *
        (
            (
                df["close"] -
                low2
            )
            /
            r2
        )
        * 100
    ) - 100

    # --------------------------------------------------------
    # SHIFT / R2
    # --------------------------------------------------------

    shift2bar_to_r2 = (
        src_shift /
        r2
    ) * 100

    # --------------------------------------------------------
    # SHIFT RATIO
    # --------------------------------------------------------

    shift_avg = get_average(
        abs(src_shift),
        LOOKBACK,
        "SMA"
    )

    srcshift_ratio_n = (
        normalize(
            abs(src_shift),
            shift_avg
        )
        * 100
        * sign_shift
    )

    # --------------------------------------------------------
    # PRICE ACTION AVERAGE
    # --------------------------------------------------------

    pricea_n = (
        s2r
        +
        barclosing
        +
        bar_spread_ratio_n
        +
        barclosing_2
        +
        shift2bar_to_r2
        +
        srcshift_ratio_n
    ) / 6

    # --------------------------------------------------------
    # BAR FLOW
    # --------------------------------------------------------

    bar_flow = (
        pricea_n *
        vola_n /
        100
    )

    # --------------------------------------------------------
    # BULL / BEAR
    # --------------------------------------------------------

    bulls = bar_flow.clip(
        lower=0
    )

    bears = (
        -bar_flow.clip(
            upper=0
        )
    )

    # --------------------------------------------------------
    # AVERAGE
    # --------------------------------------------------------

    bulls_avg = get_average(
        bulls,
        RROF_LENGTH,
        RROF_MA_TYPE
    )

    bears_avg = get_average(
        bears,
        RROF_LENGTH,
        RROF_MA_TYPE
    )

    # --------------------------------------------------------
    # DX
    # --------------------------------------------------------

    bears_avg = bears_avg.replace(
        0,
        np.nan
    )

    dx = (
        bulls_avg /
        bears_avg
    )

    # --------------------------------------------------------
    # RROF
    # --------------------------------------------------------

    rrof = (
        2
        *
        (
            100 -
            (
                100 /
                (1 + dx)
            )
        )
    ) - 100

    # --------------------------------------------------------
    # RROF SMOOTH
    # --------------------------------------------------------

    rrof_s = get_average(
        rrof,
        SMOOTH,
        "WMA"
    )

    # --------------------------------------------------------
    # SIGNAL
    # --------------------------------------------------------

    signal = get_average(
        rrof_s,
        SIGNAL_LENGTH,
        SIGNAL_MA_TYPE
    )

    df["RROF"] = rrof
    df["RROF_S"] = rrof_s
    df["SIGNAL"] = signal

    return df


# ============================================================
# SIGNAL
# ============================================================

def check_signal(df):

    # Cần ít nhất vài nến
    if len(df) < 10:

        return None

    # --------------------------------------------------------
    # Dùng nến ĐÃ ĐÓNG
    #
    # [-1] = nến đang chạy (chưa đóng)
    # [-2] = nến vừa đóng
    # [-3] = nến đóng trước đó
    # --------------------------------------------------------

    previous = df.iloc[-3]
    current = df.iloc[-2]

    print()
    print("=" * 70)
    print("📊 RROF STATUS")
    print("=" * 70)

    print(
        f"Previous: RROF_S={previous['RROF_S']:.6f}"
        f" SIGNAL={previous['SIGNAL']:.6f}"
    )

    print(
        f"Current : RROF_S={current['RROF_S']:.6f}"
        f" SIGNAL={current['SIGNAL']:.6f}"
    )

    print(
        f"Volume  : {current['volume']:,.4f}"
    )
    
    print(
        f"Price   : {current['close']:.2f}"
    )

    # --------------------------------------------------------
    # CROSS UP
    # --------------------------------------------------------

    if (
        previous["RROF_S"] <= previous["SIGNAL"]
        and
        current["RROF_S"] > current["SIGNAL"]
    ):

        return "LONG"

    # --------------------------------------------------------
    # CROSS DOWN
    # --------------------------------------------------------

    if (
        previous["RROF_S"] >= previous["SIGNAL"]
        and
        current["RROF_S"] < current["SIGNAL"]
    ):

        return "SHORT"

    return None


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("🚀 XAUUSDT RROF BINANCE SCANNER")
    print("================================")

    df = load_data()

    if df is None:

        print("❌ Không lấy được dữ liệu")

        sys.exit(1)

    # --------------------------------------------------------
    # EVEREX
    # --------------------------------------------------------

    df = calculate_everex(df)

    # --------------------------------------------------------
    # CHECK NaN
    # --------------------------------------------------------

    valid = df[
        ["RROF", "RROF_S", "SIGNAL"]
    ].dropna()

    if len(valid) < 10:

        print(
            "❌ Không đủ dữ liệu để tính EVEREX"
        )

        sys.exit(1)

    # --------------------------------------------------------
    # SIGNAL
    # --------------------------------------------------------

    signal = check_signal(df)

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    if signal == "LONG":

        message = (
            "🟢 <b>XAUUSDT RROF LONG</b>\n\n"
            f"⏱ Timeframe: 30m (Binance)\n"
            f"📊 RROF Smooth: "
            f"{df.iloc[-2]['RROF_S']:.2f}\n"
            f"📈 Signal: "
            f"{df.iloc[-2]['SIGNAL']:.2f}\n"
            f"💰 Close: "
            f"{df.iloc[-2]['close']:.2f}\n"
            f"📊 Volume: "
            f"{df.iloc[-2]['volume']:,.2f}\n\n"
            "🔔 RROF Smooth CROSS UP Signal"
        )

        send_telegram(message)

    elif signal == "SHORT":

        message = (
            "🔴 <b>XAUUSDT RROF SHORT</b>\n\n"
            f"⏱ Timeframe: 30m (Binance)\n"
            f"📊 RROF Smooth: "
            f"{df.iloc[-2]['RROF_S']:.2f}\n"
            f"📉 Signal: "
            f"{df.iloc[-2]['SIGNAL']:.2f}\n"
            f"💰 Close: "
            f"{df.iloc[-2]['close']:.2f}\n"
            f"📊 Volume: "
            f"{df.iloc[-2]['volume']:,.2f}\n\n"
            "🔔 RROF Smooth CROSS DOWN Signal"
        )

        send_telegram(message)

    else:

        print()
        print("🚫 NO NEW SIGNAL")


if __name__ == "__main__":
    main()
