from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import requests
import json
from io import BytesIO
import warnings
import re

import yfinance as yf
warnings.filterwarnings("ignore")

app = FastAPI()

# ============================
# CORS 設定
# ============================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://yt-f6d34a22-537c-e881-530f-f9e7a956a78b.github.io"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ============================
# 外部ファイル URL（Raw）
# ============================
BASE_URL = "https://raw.githubusercontent.com/yt-F6D34A22-537C-E881-530F-F9E7A956A78B/batches/main/data/"

DATA_JSON_URL = BASE_URL + "data.json"
EXCEL_URL = BASE_URL + "data_j.xlsx"

# ============================
# GitHub API URL（BASE_URL から抽出）
# ============================
# BASE_URL 例:
# https://raw.githubusercontent.com/<USER>/<REPO>/<BRANCH>/data/
m = re.match(r"https://raw.githubusercontent.com/([^/]+)/([^/]+)/([^/]+)/data/", BASE_URL)
if not m:
    raise ValueError("Invalid BASE_URL format")

repo_user = m.group(1)
repo_name = m.group(2)
branch = m.group(3)

# heuristics フォルダの GitHub API URL
API_ROOT_HEURISTICS = f"https://api.github.com/repos/{repo_user}/{repo_name}/contents/data/heuristics"

# ============================
# データ読み込み
# ============================
ticker_list = []
data_json = {}

def load_ticker_list():
    global ticker_list
    resp = requests.get(EXCEL_URL)
    resp.raise_for_status()
    df = pd.read_excel(BytesIO(resp.content))
    ticker_list = df.to_dict(orient="records")

def load_data_json():
    global data_json
    resp = requests.get(DATA_JSON_URL)
    resp.raise_for_status()
    data_json = json.loads(resp.text)

load_ticker_list()
load_data_json()

# ============================
# /dates（プルダウン用）
# ============================
@app.get("/dates")
def get_dates():
    all_dates = set()
    for symbol, entry in data_json.items():
        if isinstance(entry, dict):
            for d in entry.keys():
                if d.isdigit():
                    all_dates.add(d)
    return sorted(all_dates, reverse=True)

# ============================
# /heuristics_dates
# ============================
@app.get("/heuristics_dates")
def get_heuristics_dates():
    """
    GitHub API を利用して data/heuristics/YYYYMM/heuristics_YYYYMMDD.json を探索し、
    存在する YYYYMMDD の一覧を降順で返す。
    """
    try:
        api_root = API_ROOT_HEURISTICS

        # 1. YYYYMM フォルダ一覧
        resp = requests.get(api_root)
        resp.raise_for_status()
        folders = resp.json()

        ym_folders = [f["name"] for f in folders if re.match(r"^\d{6}$", f["name"])]
        ym_folders.sort()

        all_dates = []

        # 2. 各 YYYYMM フォルダ内の heuristics_YYYYMMDD.json を列挙
        for ym in ym_folders:
            resp2 = requests.get(f"{api_root}/{ym}")
            resp2.raise_for_status()
            files = resp2.json()

            for f in files:
                m = re.match(r"^heuristics_(\d{8})\.json$", f["name"])
                if m:
                    all_dates.append(m.group(1))

        # 3. 降順で返す
        return sorted(all_dates, reverse=True)

    except Exception as e:
        return {"error": f"failed to load heuristics dates: {str(e)}"}

# ============================
# /screening（ratio + date_ranking + heuristics）
# ============================
@app.get("/screening")
def screening(
    mode: str = "ratio",
    volume_ratio: float = 5,
    shadow_ratio: float = 5,
    target_date: str = None
):
    results = []

    # ----------------------------
    # モード A：出来高 × 上髭
    # ----------------------------
    if mode == "ratio":
        for row in ticker_list:
            code = str(row["コード"])
            name = row["銘柄名"]
            symbol = f"{code}.T"

            if symbol not in data_json:
                continue

            entry = data_json[symbol]
            if not isinstance(entry, dict):
                continue

            dates = sorted([d for d in entry.keys() if d.isdigit()])

            if target_date and target_date in dates:
                idx = dates.index(target_date)
                if idx == 0:
                    continue
                today_key = dates[idx]
                prev_key = dates[idx - 1]
            else:
                if len(dates) < 2:
                    continue
                today_key = dates[-1]
                prev_key = dates[-2]

            today = entry.get(today_key)
            prev = entry.get(prev_key)

            if not today or not prev:
                continue

            try:
                prev_vol = prev.get("v")
                today_vol = today.get("v")

                if not prev_vol or prev_vol <= 0:
                    continue

                vol_ratio_val = today_vol / prev_vol

                high = today.get("h")
                open_ = today.get("o")
                close = today.get("c")

                if high is None or open_ is None or close is None:
                    continue

                upper_shadow = high - max(open_, close)
                real_body = abs(close - open_)

                if real_body <= 0:
                    continue

                shadow_ratio_val = upper_shadow / real_body

                if vol_ratio_val >= volume_ratio and shadow_ratio_val >= shadow_ratio:
                    results.append({
                        "コード": code,
                        "銘柄名": name,
                        "出来高倍率": round(vol_ratio_val, 2),
                        "上髭実体比": round(shadow_ratio_val, 2),
                        "出来高": int(today_vol),
                        "上髭": round(upper_shadow, 2),
                        "実体": round(real_body, 2),
                    })

            except Exception:
                continue

        results.sort(key=lambda x: x["コード"])
        return results

    # ----------------------------
    # モード B：値上がり率ランキング
    # ----------------------------
    elif mode == "date_ranking":
        if not target_date:
            return {"error": "target_date is required"}

        for row in ticker_list:
            code = str(row["コード"])
            name = row["銘柄名"]
            symbol = f"{code}.T"

            if symbol not in data_json:
                continue

            entry = data_json[symbol]
            if not isinstance(entry, dict):
                continue

            dates = sorted([d for d in entry.keys() if d.isdigit()])
            if target_date not in dates:
                continue

            idx = dates.index(target_date)
            if idx == 0:
                continue

            prev_key = dates[idx - 1]

            today = entry[target_date]
            prev = entry[prev_key]

            if not today or not prev:
                continue

            try:
                today_close = today.get("c")
                prev_close = prev.get("c")

                if not prev_close or prev_close <= 0:
                    continue

                change_rate = (today_close - prev_close) / prev_close * 100

                results.append({
                    "コード": code,
                    "銘柄名": name,
                    "値上がり率": round(change_rate, 2),
                    "当日終値": today_close,
                    "前日終値": prev_close,
                    "日付": target_date
                })

            except Exception:
                continue

        results.sort(key=lambda x: x["値上がり率"], reverse=True)
        return results[:100]

    # ----------------------------
    # モード C：heuristics（配列形式 + target_date）
    # ----------------------------
    elif mode == "heuristics":
        try:
            # ----------------------------
            # 1. target_date が指定された場合
            # ----------------------------
            if target_date:
                if not re.match(r"^\d{8}$", target_date):
                    return {"error": "invalid target_date format (expected YYYYMMDD)"}

                yyyymm = target_date[:6]
                file_name = f"heuristics_{target_date}.json"

                # API でファイル一覧を取得
                resp = requests.get(f"{API_ROOT_HEURISTICS}/{yyyymm}")
                if resp.status_code != 200:
                    return {"error": f"folder {yyyymm} not found"}

                files = resp.json()
                match = next((f for f in files if f["name"] == file_name), None)
                if not match:
                    return {"error": f"heuristics file not found for {target_date}"}

                # download_url を使って JSON を取得
                raw_url = match["download_url"]
                resp2 = requests.get(raw_url)
                resp2.raise_for_status()
                raw_dict = json.loads(resp2.text)

                # 配列形式に変換
                array_data = []
                for code, tech in raw_dict.items():
                    name = next((r["銘柄名"] for r in ticker_list if str(r["コード"]) == code), "")
                    array_data.append({
                        "コード": code,
                        "銘柄名": name,
                        **tech
                    })

                return {
                    "target_date": target_date,
                    "data": array_data
                }

            # ----------------------------
            # 2. target_date が無い場合 → 最新日付を返す
            # ----------------------------
            resp = requests.get(API_ROOT_HEURISTICS)
            resp.raise_for_status()
            folders = resp.json()

            ym_folders = [f["name"] for f in folders if re.match(r"^\d{6}$", f["name"])]
            if not ym_folders:
                return {"error": "no heuristics folders found"}

            latest_ym = sorted(ym_folders)[-1]

            resp2 = requests.get(f"{API_ROOT_HEURISTICS}/{latest_ym}")
            resp2.raise_for_status()
            files = resp2.json()

            pattern = re.compile(r"^heuristics_(\d{8})\.json$")
            dated_files = [f for f in files if pattern.match(f["name"])]

            if not dated_files:
                return {"error": "no heuristics json found in latest folder"}

            latest_file = sorted(dated_files, key=lambda x: x["name"])[-1]
            latest_date = latest_file["name"].replace("heuristics_", "").replace(".json", "")

            raw_url = latest_file["download_url"]
            resp3 = requests.get(raw_url)
            resp3.raise_for_status()
            raw_dict = json.loads(resp3.text)

            array_data = []
            for code, tech in raw_dict.items():
                name = next((r["銘柄名"] for r in ticker_list if str(r["コード"]) == code), "")
                array_data.append({
                    "コード": code,
                    "銘柄名": name,
                    **tech
                })

            return {
                "target_date": latest_date,
                "data": array_data
            }

        except Exception as e:
            return {"error": f"failed to load heuristics: {str(e)}"}

    else:
        return {"error": "invalid mode"}

# ============================
# /chart（週足・月足は日足から生成）
# ============================
@app.get("/chart")
def chart(ticker: str, timeframe: str = "1d"):
    symbol = f"{ticker}.T"

    # 日足を長期間取得
    df = yf.download(symbol, period="6000d", interval="1d", progress=False)
    if df.empty:
        return {"error": "no data"}

    # MultiIndex 対応
    if isinstance(df.columns, pd.MultiIndex):
        try:
            df = df.xs(symbol, level=1, axis=1)
        except Exception:
            pass

    # DatetimeIndex を保証
    df.index = pd.to_datetime(df.index, errors="coerce")
    df = df.dropna(subset=["Open", "High", "Low", "Close"])

    # ---- 週足（W-FRI）----
    df_week = df.resample("W-FRI").agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum"
    }).dropna(subset=["Open", "Close"])

    # ---- 月足（ME：Month-End）----
    df_month = df.resample("ME").agg({
        "Open": "first",
        "High": "max",
        "Low": "min",
        "Close": "last",
        "Volume": "sum"
    }).dropna(subset=["Open", "Close"])

    # ---- timeframe に応じて返す ----
    if timeframe == "1d":
        df_out = df.tail(200)
    elif timeframe == "1wk":
        df_out = df_week.tail(200)
    elif timeframe == "1mo":
        df_out = df_month.tail(200)
    else:
        return {"error": "invalid timeframe"}

    df_out.index = df_out.index.strftime("%Y-%m-%d")

    return {
        "Open": df_out["Open"].to_dict(),
        "High": df_out["High"].to_dict(),
        "Low": df_out["Low"].to_dict(),
        "Close": df_out["Close"].to_dict(),
        "Volume": df_out["Volume"].to_dict(),
    }
