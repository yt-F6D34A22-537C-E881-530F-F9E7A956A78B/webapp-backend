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
RAW_HEURISTICS_PREFIX = BASE_URL + "heuristics/"

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

# GitHub trees API（全ファイル一覧を1回で取得）
GIT_TREE_API = f"https://api.github.com/repos/{repo_user}/{repo_name}/git/trees/{branch}?recursive=1"

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
    GitHub trees API を 1 回だけ叩いて、
    data/heuristics/**/heuristics_YYYYMMDD.json をすべて抽出する。
    """
    try:
        resp = requests.get(GIT_TREE_API)
        if resp.status_code != 200:
            return []

        tree = resp.json().get("tree", [])
        dates = []

        for item in tree:
            path = item.get("path", "")
            # data/heuristics/202606/heuristics_20260615.json
            m = re.match(r"data/heuristics/\d{6}/heuristics_(\d{8})\.json$", path)
            if m:
                dates.append(m.group(1))

        return sorted(dates, reverse=True)

    except Exception:
        return []

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
    # モード C：heuristics
    # ----------------------------
    elif mode == "heuristics":
        if not target_date:
            return {"error": "target_date is required"}

        try:
            yyyymm = target_date[:6]
            raw_url = f"{RAW_HEURISTICS_PREFIX}{yyyymm}/heuristics_{target_date}.json"

            resp = requests.get(raw_url)
            if resp.status_code != 200:
                return {"error": f"heuristics file not found for {target_date}"}

            raw_dict = json.loads(resp.text)

            array_data = []
            for code, tech in raw_dict.items():
                code_str = str(code)

                # ticker_list のコードと一致させる
                name = next((r["銘柄名"] for r in ticker_list if str(r["コード"]) == code_str), "")

                array_data.append({
                    "コード": code_str,  # ← ".T" を付けない
                    "銘柄名": name,
                    **tech
                })

            return {
                "target_date": target_date,
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
    
