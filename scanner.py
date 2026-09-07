```python
import os
import sys
import time
import requests
import pandas as pd
import numpy as np


# ============================================================
# CONFIG
# ============================================================

SYMBOL = "XAU-USDT-SWAP"

TIMEFRAME = "15m"

CANDLE_LIMIT = 500

# OKX API
OKX_BASE_URL = "https://openapi.okx.com"

OKX_CANDLES_URL = (
    f"{OKX_BASE_URL}/api/v5/market/history-candles"
)

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
# TELEGRAM CHECK
# ============================================================

def check_telegram_config():

    if not BOT_TOKEN:
        print("⚠️ TELEGRAM_BOT_TOKEN chưa được thiết lập")

    if not CHAT_ID:
        print("⚠️ TELEGRAM_CHAT_ID chưa được thiết lập")


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not BOT_TOKEN or not CHAT_ID:

        print("⚠️ Không gửi Telegram:")
        print(message)

        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{BOT_TOKEN}/sendMessage"
    )

    data = {
        "chat_id": CHAT_ID,
        "text": message
    }

    try:

        response = requests.post(
            url,
            json=data,
            timeout=20
        )

        print(
            f"📨 Telegram HTTP: "
            f"{response.status_code}"
        )

        if response.status_code != 200:

            print(
                "❌ Telegram response:"
            )

            print(response.text)

            return False

        print("✅ Telegram sent")

        return True

    except Exception as e:

        print(
            f"❌ Telegram error: {e}"
        )

        return False


# ============================================================
# WMA
# ============================================================

def wma(series, length):

    weights = np.arange(
        1,
        length + 1
    )

    return series.rolling(
        length
    ).apply(
        lambda x:
        np.dot(
            x,
            weights
        ) / weights.sum(),
        raw=True
    )


# ============================================================
# MOVING AVERAGE
# ============================================================

def get_average(
    series,
    length,
    ma_type
):

    ma_type = ma_type.upper()

    if ma_type == "SMA":

        return series.rolling(
            length
        ).mean()

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

        return wma(
            series,
            length
        )

    else:

        raise ValueError(
            f"Unsupported MA type: {ma_type}"
        )


# ============================================================
# EVEREX NORMALIZE
# ============================================================

def normalize(
    value,
    average
):

    average = average.replace(
        0,
        np.nan
    )

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
# GET OKX CANDLES
# ============================================================

def get_okx_candles():

    print()
    print("=" * 70)
    print("📥 OKX XAU-USDT-SWAP")
    print("=" * 70)

    print(
        f"Symbol    : {SYMBOL}"
    )

    print(
        f"Timeframe : {TIMEFRAME}"
    )

    print(
        f"Limit     : {CANDLE_LIMIT}"
    )

    params = {

        "instId": SYMBOL,

        "bar": TIMEFRAME,

        "limit": str(CANDLE_LIMIT)

    }

    headers = {

        "User-Agent":
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/120 Safari/537.36",

        "Accept":
        "application/json"

    }

    print()
    print(
        "▶️ Calling OKX API..."
    )

    try:

        response = requests.get(

            OKX_CANDLES_URL,

            params=params,

            headers=headers,

            timeout=30
        )

    except requests.RequestException as e:

        raise Exception(
            f"OKX connection error: {e}"
        )

    print(
        f"HTTP Status: "
        f"{response.status_code}"
    )

    if response.status_code != 200:

        print(response.text)

        raise Exception(
            f"OKX HTTP error "
            f"{response.status_code}"
        )

    try:

        data = response.json()

    except Exception:

        print(response.text)

        raise Exception(
            "OKX trả về dữ liệu "
            "không phải JSON"
        )

    if data.get("code") != "0":

        raise Exception(

            "OKX API Error: "
            f"{data.get('msg', 'Unknown error')}"

        )

    candles = data.get(
        "data",
        []
    )

    if not candles:

        raise Exception(
            "OKX không trả về candles"
        )

    print(
        f"✅ Raw candles: "
        f"{len(candles)}"
    )

    # ========================================================
    # OKX CANDLE FORMAT
    #
    # [
    # timestamp,
    # open,
    # high,
    # low,
    # close,
    # volume,
    # volume_currency,
    # volume_quote,
    # confirm
    # ]
    # ========================================================

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

        raise Exception(
            "Không parse được dữ liệu OKX"
        )

    df = pd.DataFrame(rows)

    # ========================================================
    # NUMERIC
    # ========================================================

    numeric_columns = [

        "open",
        "high",
        "low",
        "close",
        "volume",
        "volume_currency",
        "volume_quote"

    ]

    for column in numeric_columns:

        df[column] = pd.to_numeric(

            df[column],

            errors="coerce"

        )

    # ========================================================
    # TIMESTAMP
    # ========================================================

    df["timestamp"] = pd.to_datetime(

        pd.to_numeric(
            df["timestamp"],
            errors="coerce"
        ),

        unit="ms",

        utc=True

    )

    # ========================================================
    # SORT
    # ========================================================

    df = df.sort_values(
        "timestamp"
    )

    df = df.drop_duplicates(
        subset=["timestamp"]
    )

    df = df.reset_index(
        drop=True
    )

    # ========================================================
    # REMOVE INVALID
    # ========================================================

    df = df.dropna(
        subset=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]
    )

    # ========================================================
    # VOLUME CHECK
    # ========================================================

    print()
    print("=" * 70)
    print("🔊 OKX VOLUME CHECK")
    print("=" * 70)

    print(
        f"Volume min : "
        f"{df['volume'].min():,.4f}"
    )

    print(
        f"Volume max : "
        f"{df['volume'].max():,.4f}"
    )

    print(
        f"Volume avg : "
        f"{df['volume'].mean():,.4f}"
    )

    print(
        f"Zero       : "
        f"{(df['volume'] == 0).sum()}"
    )

    if (df["volume"] <= 0).all():

        raise Exception(
            "Volume OKX không hợp lệ"
        )

    # ========================================================
    # CONFIRM STATUS
    # ========================================================

    print()
    print("=" * 70)
    print("🕯 CANDLE STATUS")
    print("=" * 70)

    print(
        f"Latest confirm: "
        f"{df.iloc[-1]['confirm']}"
    )

    print(
        "0 = chưa đóng"
    )

    print(
        "1 = đã đóng"
    )

    # ========================================================
    # LAST CANDLES
    # ========================================================

    print()
    print("=" * 70)
    print("📋 LAST 5 CANDLES")
    print("=" * 70)

    print(

        df.tail(5)[
            [
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "confirm"
            ]
        ].to_string(index=False)

    )

    print()
    print(
        f"💰 Last price: "
        f"{df.iloc[-1]['close']:.2f}"
    )

    print(
        f"📊 Last volume: "
        f"{df.iloc[-1]['volume']:,.4f}"
    )

    return df


# ============================================================
# LOAD DATA
# ============================================================

def load_data():

    df = get_okx_candles()

    if df is None:

        return None

    # ========================================================
    # ENSURE ENOUGH DATA
    # ========================================================

    if len(df) < 100:

        raise Exception(
            f"Chỉ nhận được {len(df)} candles"
        )

    # ========================================================
    # IMPORTANT
    #
    # Không xóa nến hiện tại ở đây.
    # calculate_everex cần dữ liệu liên tục.
    # check_signal sẽ dùng -3 / -2.
    # ========================================================

    df = df.tail(
        CANDLE_LIMIT
    ).copy()

    df = df.reset_index(
        drop=True
    )

    print()
    print(
        f"✅ Using "
        f"{len(df)} candles"
    )

    print(
        f"📅 From: "
        f"{df.iloc[0]['timestamp']}"
    )

    print(
        f"📅 To  : "
        f"{df.iloc[-1]['timestamp']}"
    )

    return df


# ============================================================
# EVEREX / RROF
# ============================================================

def calculate_everex(df):

    df = df.copy()

    open_ = df["open"]

    high = df["high"]

    low = df["low"]

    close = df["close"]

    volume = df["volume"]

    # ========================================================
    # VOLUME
    # ========================================================

    vola = get_average(

        volume,

        LOOKBACK,

        LOOKBACK_MA_TYPE

    )

    vola_n = (

        normalize(
            volume,
            vola
        )
        * 100

    )

    # ========================================================
    # PRICE
    # ========================================================

    bar_spread = (
        close - open_
    )

    bar_range = (
        high - low
    )

    bar_range = bar_range.replace(
        0,
        np.nan
    )

    # ========================================================
    # R2
    # ========================================================

    r2 = (

        high.rolling(2).max()

        -

        low.rolling(2).min()

    )

    r2 = r2.replace(
        0,
        np.nan
    )

    # ========================================================
    # SHIFT
    # ========================================================

    src_shift = close.diff()

    # ========================================================
    # SIGN
    # ========================================================

    sign_spread = np.sign(
        bar_spread
    )

    sign_shift = np.sign(
        src_shift
    )

    # ========================================================
    # BAR CLOSING
    # ========================================================

    barclosing = (

        2
        *
        (
            (
                close - low
            )
            /
            bar_range
        )
        *
        100

    ) - 100

    # ========================================================
    # SPREAD / RANGE
    # ========================================================

    s2r = (

        bar_spread
        /
        bar_range

    ) * 100

    # ========================================================
    # SPREAD RATIO
    # ========================================================

    bar_spread_abs = abs(
        bar_spread
    )

    bar_spread_avg = get_average(

        bar_spread_abs,

        LOOKBACK,

        LOOKBACK_MA_TYPE

    )

    bar_spread_ratio_n = (

        normalize(

            bar_spread_abs,

            bar_spread_avg

        )
        *
        100
        *
        sign_spread

    )

    # ========================================================
    # 2 BAR CLOSING
    # ========================================================

    low2 = (

        low
        .rolling(2)
        .min()

    )

    barclosing_2 = (

        2
        *
        (
            (
                close - low2
            )
            /
            r2
        )
        *
        100

    ) - 100

    # ========================================================
    # SHIFT / R2
    # ========================================================

    shift2bar_to_r2 = (

        src_shift
        /
        r2

    ) * 100

    # ========================================================
    # SHIFT RATIO
    # ========================================================

    src_shift_abs = abs(
        src_shift
    )

    srcshift_avg = get_average(

        src_shift_abs,

        LOOKBACK,

        LOOKBACK_MA_TYPE

    )

    srcshift_ratio_n = (

        normalize(

            src_shift_abs,

            srcshift_avg

        )
        *
        100
        *
        sign_shift

    )

    # ========================================================
    # PRICE NORMALIZED
    # ========================================================

    pricea_n = (

        barclosing

        +

        s2r

        +

        bar_spread_ratio_n

        +

        barclosing_2

        +

        shift2bar_to_r2

        +

        srcshift_ratio_n

    ) / 6

    # ========================================================
    # BAR FLOW
    # ========================================================

    bar_flow = (

        pricea_n
        *
        vola_n
        /
        100

    )

    # ========================================================
    # BULLS
    # ========================================================

    bulls = bar_flow.clip(
        lower=0
    )

    # ========================================================
    # BEARS
    # ========================================================

    bears = (

        -bar_flow.clip(
            upper=0
        )

    )

    # ========================================================
    # BULLS / BEARS AVERAGE
    # ========================================================

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

    # ========================================================
    # RATIO
    # ========================================================

    bears_avg = bears_avg.replace(
        0,
        np.nan
    )

    dx = (

        bulls_avg
        /
        bears_avg

    )

    # ========================================================
    # RROF
    # ========================================================

    rrof = (

        2
        *
        (
            100
            -
            (
                100
                /
                (1 + dx)
            )
        )

    ) - 100

    # ========================================================
    # RROF SMOOTH
    # ========================================================

    rrof_s = get_average(

        rrof,

        SMOOTH,

        "WMA"

    )

    # ========================================================
    # SIGNAL
    # ========================================================

    signal = get_average(

        rrof_s,

        SIGNAL_LENGTH,

        SIGNAL_MA_TYPE

    )

    # ========================================================
    # SAVE
    # ========================================================

    df["RROF"] = rrof

    df["RROF_S"] = rrof_s

    df["SIGNAL"] = signal

    return df


# ============================================================
# SIGNAL CHECK
# ============================================================

def check_signal(df):

    if len(df) < 20:

        print(
            "⚠️ Không đủ dữ liệu"
        )

        return None

    # ========================================================
    # USE CLOSED CANDLES
    #
    # -1 = candle mới nhất
    #
    # Nếu -1 confirm=0:
    #     -2 = candle vừa đóng
    #     -3 = candle đóng trước đó
    #
    # Nếu -1 confirm=1:
    #     -1 = candle đã đóng
    #     -2 = candle trước
    #
    # Để an toàn tuyệt đối, ta tìm 2 candle confirmed
    # gần nhất.
    # ========================================================

    confirmed = df[
        df["confirm"].astype(str) == "1"
    ].copy()

    if len(confirmed) < 2:

        print(
            "⚠️ Không tìm đủ "
            "2 nến đã đóng"
        )

        return None

    previous = confirmed.iloc[-2]

    current = confirmed.iloc[-1]

    # ========================================================
    # STATUS
    # ========================================================

    print()
    print("=" * 70)
    print("📊 RROF STATUS")
    print("=" * 70)

    print(
        f"Previous candle : "
        f"{previous['timestamp']}"
    )

    print(
        f"Current candle  : "
        f"{current['timestamp']}"
    )

    print()

    print(
        f"Previous RROF_S : "
        f"{previous['RROF_S']:.6f}"
    )

    print(
        f"Previous SIGNAL  : "
        f"{previous['SIGNAL']:.6f}"
    )

    print()

    print(
        f"Current RROF_S  : "
        f"{current['RROF_S']:.6f}"
    )

    print(
        f"Current SIGNAL   : "
        f"{current['SIGNAL']:.6f}"
    )

    print()

    print(
        f"Price            : "
        f"{current['close']:.2f}"
    )

    print(
        f"Volume           : "
        f"{current['volume']:,.4f}"
    )

    # ========================================================
    # CROSS UP
    # ========================================================

    if (

        previous["RROF_S"]
        <=
        previous["SIGNAL"]

        and

        current["RROF_S"]
        >
        current["SIGNAL"]

    ):

        print()
        print(
            "🟢 CROSS UP → LONG"
        )

        return "LONG"

    # ========================================================
    # CROSS DOWN
    # ========================================================

    if (

        previous["RROF_S"]
        >=
        previous["SIGNAL"]

        and

        current["RROF_S"]
        <
        current["SIGNAL"]

    ):

        print()
        print(
            "🔴 CROSS DOWN → SHORT"
        )

        return "SHORT"

    print()
    print(
        "🚫 NO NEW SIGNAL"
    )

    return None


# ============================================================
# BUILD TELEGRAM MESSAGE
# ============================================================

def build_message(
    signal,
    current
):

    direction = (
        "🟢 LONG"
        if signal == "LONG"
        else
        "🔴 SHORT"
    )

    cross = (

        "RROF Smooth CROSS UP Signal"
        if signal == "LONG"
        else
        "RROF Smooth CROSS DOWN Signal"

    )

    message = (

        f"{direction} <b>XAU-USDT-SWAP</b>\n\n"

        f"📊 Source: OKX\n"

        f"⏱ Timeframe: {TIMEFRAME}\n\n"

        f"💰 Price: "
        f"{current['close']:.2f}\n"

        f"📊 Volume: "
        f"{current['volume']:,.4f}\n\n"

        f"RROF: "
        f"{current['RROF']:.2f}\n"

        f"RROF Smooth: "
        f"{current['RROF_S']:.2f}\n"

        f"Signal: "
        f"{current['SIGNAL']:.2f}\n\n"

        f"🕐 Candle:\n"
        f"{current['timestamp']}\n\n"

        f"🔔 {cross}"

    )

    return message


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print(
        "🚀 GOLD RROF OKX SCANNER"
    )

    print(
        "=========================================="
    )

    print(
        f"Symbol    : {SYMBOL}"
    )

    print(
        f"Timeframe : {TIMEFRAME}"
    )

    print(
        f"Candles   : {CANDLE_LIMIT}"
    )

    print(
        "=========================================="
    )

    check_telegram_config()

    # ========================================================
    # LOAD
    # ========================================================

    try:

        df = load_data()

    except Exception as e:

        print()
        print(
            f"❌ DATA ERROR: {e}"
        )

        sys.exit(1)

    if df is None:

        print(
            "❌ Không lấy được dữ liệu"
        )

        sys.exit(1)

    # ========================================================
    # EVEREX
    # ========================================================

    print()
    print(
        "🧮 Calculating EVEREX / RROF..."
    )

    try:

        df = calculate_everex(df)

    except Exception as e:

        print()
        print(
            f"❌ EVEREX ERROR: {e}"
        )

        sys.exit(1)

    # ========================================================
    # VALIDATION
    # ========================================================

    valid = df[
        [
            "RROF",
            "RROF_S",
            "SIGNAL"
        ]
    ].dropna()

    print(
        f"✅ Valid RROF rows: "
        f"{len(valid)}"
    )

    if len(valid) < 20:

        print(
            "❌ Không đủ dữ liệu "
            "để tính RROF"
        )

        sys.exit(1)

    # ========================================================
    # SIGNAL
    # ========================================================

    signal = check_signal(df)

    # ========================================================
    # TELEGRAM
    # ========================================================

    if signal in [
        "LONG",
        "SHORT"
    ]:

        confirmed = df[
            df["confirm"].astype(str) == "1"
        ]

        current = confirmed.iloc[-1]

        message = build_message(
            signal,
            current
        )

        print()
        print(
            "📨 Sending Telegram..."
        )

        send_telegram(
            message
        )

    else:

        print()
        print(
            "ℹ️ Không gửi Telegram "
            "vì không có tín hiệu mới."
        )

    print()
    print(
        "✅ Scanner completed."
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()
```
