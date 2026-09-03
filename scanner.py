import os
import sys
import subprocess
import glob
import shutil
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone


# ============================================================
# CONFIG
# ============================================================

SYMBOL = "XAUUSD"

# M30
TIMEFRAME = "m30"

# Lấy dư dữ liệu để EVEREX có đủ dữ liệu tính toán
DOWNLOAD_DAYS = 10

# Số nến cuối cùng dùng tính toán
CANDLE_LIMIT = 250

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
# DOWNLOAD DUKASCOPY DATA
# ============================================================

def download_dukascopy():

    print()
    print("=" * 70)
    print("📥 DUKASCOPY XAUUSD")
    print("=" * 70)

    # UTC
    now = datetime.now(timezone.utc)

    date_to = now.date() + timedelta(days=1)
    date_from = now.date() - timedelta(days=DOWNLOAD_DAYS)

    print(f"📅 From : {date_from}")
    print(f"📅 To   : {date_to}")
    print(f"⏱ TF   : {TIMEFRAME}")

    # Xóa file cũ
    for f in glob.glob("xauusd-*.csv"):
        try:
            os.remove(f)
        except Exception:
            pass

    command = [
        "npx",
        "--yes",
        "dukascopy-node",
        "-i",
        "xauusd",
        "-from",
        str(date_from),
        "-to",
        str(date_to),
        "-t",
        TIMEFRAME,
        "-f",
        "csv"
    ]

    print()
    print("▶️ Downloading Dukascopy...")

    try:

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=180
        )

        print(result.stdout)

        if result.returncode != 0:

            print("❌ Dukascopy download failed")
            print(result.stderr)

            return None

    except subprocess.TimeoutExpired:

        print("❌ Download timeout")

        return None

    except Exception as e:

        print("❌ Download error:", e)

        return None

    # Tìm CSV
    files = glob.glob("*.csv")

    # Loại bỏ file không liên quan
    files = [
        f for f in files
        if "xauusd" in f.lower()
    ]

    if not files:

        print("❌ Không tìm thấy file XAUUSD CSV")

        print("Files hiện có:")

        for f in glob.glob("*"):
            print("   ", f)

        return None

    print()
    print("📁 CSV found:")

    for f in files:
        print("   ", f)

    # Nếu chỉ một file
    if len(files) == 1:

        return files[0]

    # Ghép nhiều file
    dfs = []

    for f in files:

        try:

            temp = pd.read_csv(f)

            dfs.append(temp)

        except Exception as e:

            print(f"⚠️ Không đọc được {f}: {e}")

    if not dfs:
        return None

    df = pd.concat(
        dfs,
        ignore_index=True
    )

    output = "xauusd_combined.csv"

    df.to_csv(
        output,
        index=False
    )

    return output


# ============================================================
# LOAD DATA
# ============================================================

def load_dukascopy_data():

    file = download_dukascopy()

    if not file:
        return None

    print()
    print("=" * 70)
    print("📊 LOAD DUKASCOPY DATA")
    print("=" * 70)

    try:

        df = pd.read_csv(file)

    except Exception as e:

        print("❌ CSV read error:", e)

        return None

    print("Columns:")
    print(df.columns.tolist())

    # --------------------------------------------------------
    # timestamp
    # --------------------------------------------------------

    if "timestamp" not in df.columns:

        print("❌ Không có timestamp")

        return None

    df["datetime"] = pd.to_datetime(
        df["timestamp"],
        unit="ms",
        utc=True
    )

    # --------------------------------------------------------
    # numeric
    # --------------------------------------------------------

    required = [
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]

    for col in required:

        if col not in df.columns:

            print(
                f"❌ Thiếu cột bắt buộc: {col}"
            )

            return None

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    # --------------------------------------------------------
    # REMOVE INVALID
    # --------------------------------------------------------

    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]
    )

    df = df.sort_values(
        "datetime"
    )

    df = df.drop_duplicates(
        subset=["datetime"]
    )

    # --------------------------------------------------------
    # VOLUME CHECK
    # --------------------------------------------------------

    if df["volume"].isna().any():

        print("❌ Volume chứa NaN")

        return None

    if (df["volume"] <= 0).all():

        print("❌ Volume không hợp lệ")

        return None

    print()
    print(f"✅ Loaded: {len(df)} candles")

    print(
        f"📅 {df['datetime'].iloc[0]}"
        f" → {df['datetime'].iloc[-1]}"
    )

    print(
        f"📊 Volume min : {df['volume'].min():,.4f}"
    )

    print(
        f"📊 Volume max : {df['volume'].max():,.4f}"
    )

    print(
        f"📊 Volume avg : {df['volume'].mean():,.4f}"
    )

    # Chỉ lấy 250 nến cuối
    df = df.tail(CANDLE_LIMIT).copy()

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
    # -1 = nến mới nhất
    # -2 = nến trước
    #
    # Vì dữ liệu Dukascopy có thể đang chứa nến hiện tại,
    # chúng ta dùng -3 và -2 để tránh nến đang chạy.
    # --------------------------------------------------------

    previous = df.iloc[-3]
    current = df.iloc[-2]

    print()
    print("=" * 70)
    print("📊 RROF STATUS")
    print("=" * 70)

    print(
        "Previous:"
        f" RROF_S={previous['RROF_S']:.6f}"
        f" SIGNAL={previous['SIGNAL']:.6f}"
    )

    print(
        "Current :"
        f" RROF_S={current['RROF_S']:.6f}"
        f" SIGNAL={current['SIGNAL']:.6f}"
    )

    print(
        f"Volume  : {current['volume']:,.4f}"
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
    print("🚀 GOLD RROF DUKASCOPY SCANNER")
    print("================================")

    df = load_dukascopy_data()

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
            "🟢 <b>XAUUSD RROF LONG</b>\n\n"
            f"⏱ Timeframe: {TIMEFRAME.upper()}\n"
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
            "🔴 <b>XAUUSD RROF SHORT</b>\n\n"
            f"⏱ Timeframe: {TIMEFRAME.upper()}\n"
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
