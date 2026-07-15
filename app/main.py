from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import requests
import json
from io import BytesIO
import warnings
import re
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from app.heuristics_scoring import calc_heuristics_score

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
# GitHub Token（環境変数から取得）
# ============================
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

def github_headers():
    """GitHub API 用ヘッダ（Token があれば付与）"""
    headers = {}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    return headers

# ============================
# 外部ファイル URL（Raw）
# ============================
BASE_URL = "https://raw.githubusercontent.com/yt-F6D34A22-537C-E881-530F-F9E7A956A78B/batches/refs/heads/main/data/"

DATA_JSON_URL = BASE_URL + "data.json"
EXCEL_URL = BASE_URL + "data_j.xlsx"
RAW_HEURISTICS_PREFIX = BASE_URL + "heuristics/"

# ============================
# GitHub API URL（BASE_URL から抽出）
# ============================
# BASE_URL 例:
# https://raw.githubusercontent.com/<USER>/<REPO>/<BRANCH...>/data/
m = re.match(r"https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/(.+?)/data/", BASE_URL)
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
trading_dates_cache = []  # /trading_dates 用（3か月分の市場開場日。起動時に1回だけ取得）

def load_ticker_list():
    global ticker_list
    try:
        resp = requests.get(EXCEL_URL)
        resp.raise_for_status()
        df = pd.read_excel(BytesIO(resp.content))
        ticker_list = df.to_dict(orient="records")
    except Exception as e:
        print("Failed to load ticker list:", e)
        ticker_list = []

def load_data_json():
    global data_json
    try:
        resp = requests.get(DATA_JSON_URL)
        resp.raise_for_status()
        data_json = json.loads(resp.text)
    except Exception as e:
        print("Failed to load data.json:", e)
        data_json = {}

def load_trading_dates():
    """
    直近3か月の市場開場日一覧を取得し、trading_dates_cache に格納する。
    yfinance によるYahoo Financeへの外部通信は数秒〜十数秒かかることがあり、
    これをリクエスト処理のたびに行うと、Renderのコールドスタート（dyno起動）に
    かかる時間と合算してタイムアウトしてしまう不具合があった（2026-07 修正）。
    load_ticker_list() / load_data_json() と同様に、起動時（モジュール読み込み時）に
    1回だけ実行してキャッシュし、/trading_dates はキャッシュを返すだけにする。
    """
    global trading_dates_cache
    try:
        index_symbol = "^N225"
        df = yf.download(index_symbol, period="3mo", interval="1d", progress=False)
        if df.empty:
            print("Failed to load trading dates: empty data")
            trading_dates_cache = []
            return

        df.index = pd.to_datetime(df.index, errors="coerce")
        df = df.dropna(how="all")

        trading_dates_cache = sorted(df.index.strftime("%Y%m%d").tolist(), reverse=True)
    except Exception as e:
        print("Failed to load trading dates:", e)
        trading_dates_cache = []

load_ticker_list()
load_data_json()
load_trading_dates()

# ============================
# ユーティリティ
# ============================
def parse_exclude_markets(exclude_markets: str) -> set:
    """カンマ区切りの除外市場文字列を set に変換する"""
    if not exclude_markets:
        return set()
    return {m.strip() for m in exclude_markets.split(",") if m.strip()}

# compare モード：銘柄ごとの終値取得（yfinance 通信）を並列実行する際の
# 最大同時実行数。大きくしすぎると Yahoo Finance 側のレート制限に
# 抵触しやすくなるため、あえて小さめの値に固定している。
COMPARE_FETCH_MAX_WORKERS = 8

def fetch_close_price(code: str, date: str) -> float | None:
    """
    指定銘柄コードの指定日（YYYYMMDD）の終値を取得する。
    data.json の保持範囲（直近10日）を超える日付にも対応するため
    yfinance から個別取得する。
    """
    try:
        symbol = f"{code}.T"
        target = pd.to_datetime(date, format="%Y%m%d")
        # 土日祝を跨ぐ可能性を考慮し前後の余裕を持たせて取得
        df = yf.download(
            symbol,
            start=target - pd.Timedelta(days=7),
            end=target + pd.Timedelta(days=1),
            interval="1d",
            progress=False,
        )
        if df.empty:
            return None

        if isinstance(df.columns, pd.MultiIndex):
            try:
                df = df.xs(symbol, level=1, axis=1)
            except Exception:
                pass

        df.index = pd.to_datetime(df.index, errors="coerce")
        df = df.dropna(subset=["Close"])

        match = df[df.index.strftime("%Y%m%d") == date]
        if match.empty:
            return None

        return round(float(match["Close"].iloc[0]), 2)

    except Exception:
        return None

def fetch_close_prices_range(code: str, from_date: str, to_date: str) -> dict[str, float]:
    """
    指定銘柄コードの from_date〜to_date（両端含む）の終値を、
    yfinance から1回の呼び出しでまとめて取得する。
    compare モードの all_market_days=true（全営業日比較）専用。
    fetch_close_price(code, date) を対象日数ぶんループすると
    銘柄数×対象日数の外部通信が発生し、Yahoo Finance のレート制限や
    Renderのタイムアウトを招くリスクが高いため、範囲取得1回に集約する
    （/trading_dates が同様の理由でキャッシュ化された経緯と同じ配慮）。
    取得失敗時は空 dict を返す。
    """
    try:
        symbol = f"{code}.T"
        start = pd.to_datetime(from_date, format="%Y%m%d")
        end = pd.to_datetime(to_date, format="%Y%m%d") + pd.Timedelta(days=1)

        df = yf.download(symbol, start=start, end=end, interval="1d", progress=False)
        if df.empty:
            return {}

        if isinstance(df.columns, pd.MultiIndex):
            try:
                df = df.xs(symbol, level=1, axis=1)
            except Exception:
                pass

        df.index = pd.to_datetime(df.index, errors="coerce")
        df = df.dropna(subset=["Close"])

        return {
            d: round(float(c), 2)
            for d, c in zip(df.index.strftime("%Y%m%d"), df["Close"])
        }
    except Exception:
        return {}

def _fetch_compare_prices(code: str, from_date: str, to_date: str, all_market_days: bool):
    """
    compare モード：1銘柄ぶんの終値取得（I/O部分のみ）。
    ThreadPoolExecutor から並列に呼び出されることを前提とした関数のため、
    ticker_list 照合や予測ロジックなど、外部通信を伴わない処理は含めない
    （それらは呼び出し側で従来通り直列に行う）。
    戻り値: (from_close, close_map)
      - all_market_days=False: close_map は {to_date: to_close}（取得失敗時は空 dict）
      - all_market_days=True : close_map は fetch_close_prices_range の結果
    """
    from_close = fetch_close_price(code, from_date)

    if all_market_days:
        close_map = fetch_close_prices_range(code, from_date, to_date)
    else:
        to_close = fetch_close_price(code, to_date)
        close_map = {to_date: to_close} if to_close is not None else {}

    return from_close, close_map

# ============================
# block モード（超大口検出）
# ============================
# 銘柄ごとの1分足取得（yfinance 通信）を並列実行する際の最大同時実行数。
# compare モードと同様、Yahoo Finance 側のレート制限を考慮して小さめに固定する。
BLOCK_FETCH_MAX_WORKERS = 8

# 単一バーの推定売買代金がこの金額（円）以上の場合に「超大口の可能性あり」と判定する
# デフォルト値（歩み値ベースの目視判断で使われていた「1億円」を踏襲）
BLOCK_TRADE_DEFAULT_THRESHOLD_YEN = 100_000_000

# 一次スクリーニング（日次売買代金ランキング）で1分足取得の対象とする候補数の既定値。
# 大きくするほど検出漏れは減るが、1分足取得（外部通信）の回数が比例して増える。
BLOCK_CANDIDATE_DEFAULT_LIMIT = 50

# 1分足が取得できるのは直近7日程度という Yahoo Finance 側の制約があるため、
# target_date はこの制約に収まる日付のみ受け付ける。
# trading_dates_cache は降順（新しい日付が先頭）なので、
# 先頭から数件が「1分足取得が期待できる」対象日となる。
# 7日ではなく6営業日としているのは、Yahoo側の「直近7日」が暦日ベースであるのに対し
# trading_dates_cache は営業日ベースのため、境界付近での取得失敗（休日を挟んで
# 7営業日 > 7暦日 になるケース）を避けるための安全マージン。
BLOCK_RECENT_TRADABLE_DAYS = 6

def fetch_1m_bars(code: str, target_date: str) -> pd.DataFrame | None:
    """
    指定銘柄コードの指定日（YYYYMMDD）の1分足（Open/High/Low/Close/Volume）を取得する。
    yfinance の 1分足は Yahoo Finance 側の制約により直近7日程度しか取得できない
    （BLOCK_RECENT_TRADABLE_DAYS を参照）。取得失敗・データなしの場合は None を返す。
    """
    try:
        symbol = f"{code}.T"
        target = pd.to_datetime(target_date, format="%Y%m%d")
        df = yf.download(
            symbol,
            start=target,
            end=target + pd.Timedelta(days=1),
            interval="1m",
            progress=False,
        )
        if df.empty:
            return None

        if isinstance(df.columns, pd.MultiIndex):
            try:
                df = df.xs(symbol, level=1, axis=1)
            except Exception:
                pass

        df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
        if df.empty:
            return None

        return df
    except Exception:
        return None

def detect_block_bars(df: pd.DataFrame, threshold_yen: float) -> pd.DataFrame:
    """
    1分足DataFrameから、単一バーの推定売買代金が threshold_yen 以上のバーを抽出する。
    歩み値（個々の約定）は取得できないため、「出来高 × バー内の代表値
    （高値・安値・終値の平均＝いわゆる typical price）」を1分間の約定代金の近似値として扱う。
    該当バーがなければ空のDataFrameを返す。
    """
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3
    bar_value = df["Volume"] * typical_price

    hits = df[bar_value >= threshold_yen].copy()
    if hits.empty:
        return hits

    hits["bar_value"] = bar_value[bar_value >= threshold_yen]
    hits["price_change_pct"] = (hits["Close"] - hits["Open"]) / hits["Open"] * 100
    return hits

def _fetch_block_detection(code: str, target_date: str, threshold_yen: float):
    """
    block モード：1銘柄ぶんの1分足取得・検出処理（I/O部分を含む）。
    ThreadPoolExecutor から並列に呼び出されることを前提とした関数。
    該当バーがなければ None を返す（超大口の兆候なし＝結果に含めない）。
    """
    df = fetch_1m_bars(code, target_date)
    if df is None:
        return None

    hits = detect_block_bars(df, threshold_yen)
    if hits.empty:
        return None

    # 単一バーの推定売買代金が最大の1本を代表値として採用する
    max_row = hits.loc[hits["bar_value"].idxmax()]

    # 価格変化率の絶対値が大きい（値を飛ばして約定させた）ほど「顕在的な大口」、
    # 小さい（値をほぼ動かさずに吸収した）ほど「潜在的な大口（吸収）」寄りと解釈する。
    # 0.3% は目視判断の目安として置いた暫定値であり、確定的な閾値ではない。
    trade_type = "値飛ばし" if abs(max_row["price_change_pct"]) >= 0.3 else "吸収"

    return {
        "検出件数": int(len(hits)),
        "最大売買代金": round(float(max_row["bar_value"]), 0),
        "検出時刻": max_row.name.strftime("%H:%M"),
        "価格変化率": round(float(max_row["price_change_pct"]), 2),
        "タイプ": trade_type,
    }

# ============================
# /dates（プルダウン用）
# ============================
@app.get("/dates")
def get_dates():
    try:
        all_dates = set()
        for symbol, entry in data_json.items():
            if isinstance(entry, dict):
                for d in entry.keys():
                    if d.isdigit():
                        all_dates.add(d)
        return {"status": "ok", "dates": sorted(all_dates, reverse=True)}
    except Exception as e:
        return {"error": "failed to load dates", "detail": str(e)}

# ============================
# /trading_dates（compare モードの比較元日付用）
# ============================
@app.get("/trading_dates")
def get_trading_dates():
    """
    直近3か月の市場開場日一覧を返す。
    data.json は直近10日分のみ保持のため、より長期間の開場日カレンダーが
    必要な compare モードの比較元日付セレクタ用に、
    起動時（モジュール読み込み時）に load_trading_dates() で1回だけ取得した
    trading_dates_cache を返す（2026-07 修正。以前はリクエスト毎に
    yfinance へ問い合わせていたが、Renderのコールドスタート時に
    起動遅延と外部通信時間が合算してタイムアウトする不具合があったため、
    /dates（data_json）と同じ「起動時に1回だけ取得してキャッシュする」
    方式に統一した）。
    ただし、起動時の取得自体が外部通信の失敗で空振りした場合、リトライが
    一切行われず、プロセスが再起動されるまで永久に失敗し続ける不具合が
    あったため、2026-07 にキャッシュが空の場合のみリクエスト時にその場で
    再取得を試みるフォールバックを追加した。これにより、キャッシュ済みの
    通常時は高速な応答を維持しつつ、起動時取得が失敗した場合でも
    次のリクエストで自己回復できるようにしている
    （元々の「コールドスタート時のみ失敗し、その後のリロードでは
    正常に取得できる」という挙動に近い形に戻している）。
    """
    global trading_dates_cache
    try:
        if not trading_dates_cache:
            load_trading_dates()
        if not trading_dates_cache:
            return {"error": "no trading date data"}
        return {"status": "ok", "dates": trading_dates_cache}
    except Exception as e:
        return {"error": "failed to load trading dates", "detail": str(e)}

# ============================
# /heuristics_dates
# ============================
@app.get("/heuristics_dates")
def get_heuristics_dates():
    """
    GitHub trees API を1回だけ叩き、
    data/heuristics/**/heuristics_YYYYMMDD.json を抽出。
    エラー時は詳細を返却する。
    Token を付与して rate limit を回避。
    """
    try:
        resp = requests.get(GIT_TREE_API, headers=github_headers())
        if resp.status_code != 200:
            return {
                "error": "GitHub API error",
                "status": resp.status_code,
                "detail": resp.text
            }

        tree = resp.json().get("tree", [])
        dates = []

        for item in tree:
            path = item.get("path", "")
            # data/heuristics/202606/heuristics_20260615.json
            m = re.match(r"data/heuristics/\d{6}/heuristics_(\d{8})\.json$", path)
            if m:
                dates.append(m.group(1))

        return {
            "status": "ok",
            "dates": sorted(dates, reverse=True)
        }

    except Exception as e:
        return {"error": "exception", "detail": str(e)}

# ============================
# /screening（ratio + date_ranking + heuristics）
# ============================
@app.get("/screening")
def screening(
    mode: str = "ratio",
    volume_ratio: float = 5,
    shadow_ratio: float = 5,
    target_date: str = None,
    exclude_markets: str = None,  # カンマ区切りで除外する市場・商品区分
    codes: str = None,            # heuristics 絞り込み／compare 対象銘柄（カンマ区切り）
    from_date: str = None,        # compare モード用：比較元日付
    to_date: str = None,          # compare モード用：比較先日付
    source_mode: str = None,      # compare モード用：CSV元モード（ratio/date/heuristics/空）
    all_market_days: bool = False,  # compare モード用：true の場合、比較元日付を基準に
                                     # 比較先日付までの全市場開場日と比較する
                                     # （既定 false ＝従来通りの2点比較。省略時は無改修で動作）
    threshold_yen: float = None,    # block モード用：単一バーの推定売買代金の検出閾値（円）
    candidate_limit: int = None,    # block モード用：1分足取得の対象とする候補数（日次売買代金上位）
):
    results = []

    # ----------------------------
    # モード A：出来高 × 上髭
    # ----------------------------
    if mode == "ratio":
        try:
            exclude_set = parse_exclude_markets(exclude_markets)

            for row in ticker_list:
                code = str(row["コード"])
                name = row["銘柄名"]
                symbol = code

                # 除外市場フィルタ（heuristics モードと同一ロジック）
                market = str(row.get("市場・商品区分", ""))
                if exclude_set and market in exclude_set:
                    continue

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
            return {"status": "ok", "data": results}

        except Exception as e:
            return {"error": "ratio screening failed", "detail": str(e)}

    # ----------------------------
    # モード B：値上がり率ランキング
    # ----------------------------
    elif mode == "date_ranking":
        if not target_date:
            return {"error": "target_date is required"}

        try:
            for row in ticker_list:
                code = str(row["コード"])
                name = row["銘柄名"]
                symbol = code

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
            return {"status": "ok", "data": results[:100]}

        except Exception as e:
            return {"error": "date_ranking failed", "detail": str(e)}

    # ----------------------------
    # モード C：heuristics
    # ----------------------------
    elif mode == "heuristics":
        if not target_date:
            return {"error": "target_date is required"}

        try:
            exclude_set = parse_exclude_markets(exclude_markets)

            yyyymm = target_date[:6]
            raw_url = f"{RAW_HEURISTICS_PREFIX}{yyyymm}/heuristics_{target_date}.json"

            resp = requests.get(raw_url, headers=github_headers())
            if resp.status_code != 200:
                return {
                    "error": "heuristics file not found",
                    "status": resp.status_code,
                    "url": raw_url
                }

            raw_dict = json.loads(resp.text)

            array_data = []
            for code, tech in raw_dict.items():
                code_str = str(code)

                # 銘柄名・市場区分の取得
                ticker_row = next(
                    (r for r in ticker_list if str(r["コード"]) == code_str),
                    None
                )
                if ticker_row is None:
                    continue

                # 除外市場フィルタ
                market = str(ticker_row.get("市場・商品区分", ""))
                if exclude_set and market in exclude_set:
                    continue

                name = ticker_row["銘柄名"]
                score = calc_heuristics_score(tech)

                array_data.append({
                    "コード":       code_str,
                    "銘柄名":       name,
                    "アップスコア": score["up"],
                    "ダウンスコア": score["down"],
                    "applied_up_rules":   score.get("applied_up_rules", []),
                    "applied_down_rules": score.get("applied_down_rules", []),
                    **tech
                })

            # 証券コードによる絞り込み（指定時は上位20件制限を行わず全件返却）
            codes_filter = [c.strip() for c in (codes or "").split(",") if c.strip()]

            if codes_filter:
                filtered = [d for d in array_data if d["コード"] in codes_filter]
                # その証券コードのトレンド（アップ/ダウンスコアの大きい方）を設定
                for d in filtered:
                    d["トレンド"] = "up" if d["アップスコア"] >= d["ダウンスコア"] else "down"

                return {
                    "status": "ok",
                    "target_date": target_date,
                    "data": {
                        "up":   [d for d in filtered if d["トレンド"] == "up"],
                        "down": [d for d in filtered if d["トレンド"] == "down"],
                    }
                }

            top_up   = sorted(array_data, key=lambda x: x["アップスコア"], reverse=True)[:20]
            top_down = sorted(array_data, key=lambda x: x["ダウンスコア"], reverse=True)[:20]

            return {
                "status": "ok",
                "target_date": target_date,
                "data": {
                    "up":   top_up,
                    "down": top_down,
                }
            }

        except Exception as e:
            return {"error": "heuristics failed", "detail": str(e)}

    # ----------------------------
    # モード D：CSV/証券コード比較
    # ----------------------------
    elif mode == "compare":
        if not from_date or not to_date:
            return {"error": "from_date and to_date are required"}

        try:
            codes_list = [c.strip() for c in (codes or "").split(",") if c.strip()]
            if not codes_list:
                return {"error": "codes is required"}

            # source_mode に応じた予測ロジックの切替
            # - ratio（出来高×上髭）: 全件「上昇」予測
            # - date（値上がり率ランキング）: 予測なし
            # - heuristics: from_date 時点の heuristics JSON からトレンドを取得
            # - 証券コード直接入力（source_mode未指定）: 予測なし
            heuristics_trend_map = {}
            if source_mode == "heuristics":
                yyyymm = from_date[:6]
                raw_url = f"{RAW_HEURISTICS_PREFIX}{yyyymm}/heuristics_{from_date}.json"
                resp = requests.get(raw_url, headers=github_headers())
                if resp.status_code == 200:
                    raw_dict = json.loads(resp.text)
                    for code, tech in raw_dict.items():
                        score = calc_heuristics_score(tech)
                        heuristics_trend_map[str(code)] = (
                            "up" if score["up"] >= score["down"] else "down"
                        )

            # 全営業日比較モード（all_market_days=true）の対象日付一覧。
            # 比較元日付（from_date）自身は含めない（差分が常に0になるため）。
            # #compareFromDate・#compareToDateSelect の選択肢自体が
            # /trading_dates（＝この trading_dates_cache）から構築されているため、
            # 範囲内の日付はキャッシュのみで求まり、追加の外部通信は発生しない。
            all_market_dates = []
            if all_market_days:
                all_market_dates = sorted(
                    d for d in trading_dates_cache
                    if from_date < d <= to_date
                )
                if not all_market_dates:
                    return {"error": "no trading days between from_date and to_date"}

            # ticker_list 照合を先に済ませ、無効な証券コードには
            # yfinance への通信を発生させない（従来の挙動を踏襲）
            valid_codes = []
            code_name_map = {}
            for code in codes_list:
                ticker_row = next(
                    (r for r in ticker_list if str(r["コード"]) == code),
                    None
                )
                if ticker_row is None:
                    continue
                valid_codes.append(code)
                code_name_map[code] = ticker_row["銘柄名"]

            # 銘柄ごとの終値取得（yfinance 通信）を並列実行する。
            # 直列ループだと「1銘柄あたりの通信時間 × 銘柄数」がそのまま
            # レスポンス時間になっていたが、各銘柄の取得は互いに独立しているため
            # ThreadPoolExecutor で並列化することでレスポンス時間を短縮できる。
            # 結果は codes_list の順序を保つため、一旦 price_data に集約してから
            # valid_codes の順で結果を組み立てる（futures の完了順=結果の並び順にはしない）。
            price_data = {}
            if valid_codes:
                with ThreadPoolExecutor(
                    max_workers=min(COMPARE_FETCH_MAX_WORKERS, len(valid_codes))
                ) as executor:
                    futures = {
                        executor.submit(
                            _fetch_compare_prices, code, from_date, to_date, all_market_days
                        ): code
                        for code in valid_codes
                    }
                    for future in as_completed(futures):
                        code = futures[future]
                        try:
                            price_data[code] = future.result()
                        except Exception:
                            # 1銘柄の取得失敗が他銘柄に波及しないよう、
                            # 個別に失敗扱い（取得できなかった場合と同じ状態）にする
                            price_data[code] = (None, {})

            for code in valid_codes:
                name = code_name_map[code]
                from_close, close_map = price_data.get(code, (None, {}))

                if from_close is None:
                    results.append({
                        "コード": code,
                        "銘柄名": name,
                        "error": "終値データを取得できませんでした",
                    })
                    continue

                if source_mode == "ratio":
                    prediction = "up"
                elif source_mode == "heuristics":
                    prediction = heuristics_trend_map.get(code)
                else:
                    # "date"（値上がり率ランキング）または証券コード直接入力
                    prediction = None

                # ------------------------------
                # 全営業日比較モード
                # ------------------------------
                if all_market_days:
                    for target_date in all_market_dates:
                        to_close = close_map.get(target_date)
                        if to_close is None:
                            # 比較元終値・予測は取得済みのため、対象日の終値のみ欠落した
                            # 状態として結果に含める（フロントの横持ち表示で比較元終値の
                            # 固定列を空白にしないため。2026-07 追加）
                            results.append({
                                "コード": code,
                                "銘柄名": name,
                                "比較先日付": target_date,
                                "比較元終値": from_close,
                                "予測": prediction,
                                "error": "終値データを取得できませんでした",
                            })
                            continue

                        diff_yen = round(to_close - from_close, 2)
                        diff_pct = round((to_close - from_close) / from_close * 100, 2) if from_close else None

                        results.append({
                            "コード": code,
                            "銘柄名": name,
                            "比較先日付": target_date,
                            "比較元終値": from_close,
                            "比較先終値": to_close,
                            "増減円": diff_yen,
                            "増減率": diff_pct,
                            "予測": prediction,
                        })
                    continue

                # ------------------------------
                # 既存：単一比較モード（挙動変更なし）
                # ------------------------------
                to_close = close_map.get(to_date)

                if to_close is None:
                    # 比較元終値・予測は取得済みのため、比較先終値のみ欠落した状態として
                    # 結果に含める（フロントの横持ち表示で比較元終値の固定列を
                    # 空白にしないため。2026-07 追加）
                    results.append({
                        "コード": code,
                        "銘柄名": name,
                        "比較元終値": from_close,
                        "予測": prediction,
                        "error": "終値データを取得できませんでした",
                    })
                    continue

                diff_yen = round(to_close - from_close, 2)
                diff_pct = round((to_close - from_close) / from_close * 100, 2) if from_close else None

                results.append({
                    "コード": code,
                    "銘柄名": name,
                    "比較元終値": from_close,
                    "比較先終値": to_close,
                    "増減円": diff_yen,
                    "増減率": diff_pct,
                    "予測": prediction,
                })

            return {"status": "ok", "data": results}

        except Exception as e:
            return {"error": "compare failed", "detail": str(e)}

    # ----------------------------
    # モード E：超大口検出（block）
    # ----------------------------
    elif mode == "block":
        if not target_date:
            return {"error": "target_date is required"}

        # 1分足は直近7日程度しか取得できない（Yahoo Finance 側の制約）ため、
        # 対象外の日付は1分足取得を試みる前に弾く（詳細は BLOCK_RECENT_TRADABLE_DAYS を参照）
        recent_tradable_dates = set(trading_dates_cache[:BLOCK_RECENT_TRADABLE_DAYS])
        if target_date not in recent_tradable_dates:
            return {
                "error": "target_date is out of range for 1-minute data",
                "detail": "1分足データは直近の取引日のみ取得可能です。直近の日付を選択してください。",
            }

        try:
            exclude_set = parse_exclude_markets(exclude_markets)
            threshold = threshold_yen if threshold_yen else BLOCK_TRADE_DEFAULT_THRESHOLD_YEN
            limit = candidate_limit if candidate_limit else BLOCK_CANDIDATE_DEFAULT_LIMIT

            # ------------------------------
            # 一次スクリーニング：日次売買代金（出来高×終値）による候補絞り込み
            # data.json のみを用いた in-memory 処理のため外部通信は発生しない。
            # 全銘柄（約4000銘柄）に対して毎回1分足を取得するのは非現実的
            # （Yahoo Finance のレート制限・応答時間の両面で）なため、
            # まずこの安価な処理で候補を上位 limit 件に絞り込む。
            # ------------------------------
            candidates = []
            for row in ticker_list:
                code = str(row["コード"])
                name = row["銘柄名"]

                market = str(row.get("市場・商品区分", ""))
                if exclude_set and market in exclude_set:
                    continue

                entry = data_json.get(code)
                if not isinstance(entry, dict):
                    continue

                day = entry.get(target_date)
                if not day:
                    continue

                volume = day.get("v")
                close = day.get("c")
                if not volume or not close:
                    continue

                candidates.append({
                    "コード": code,
                    "銘柄名": name,
                    "日次売買代金": volume * close,
                })

            candidates.sort(key=lambda x: x["日次売買代金"], reverse=True)
            candidates = candidates[:limit]

            if not candidates:
                return {"status": "ok", "data": []}

            # ------------------------------
            # 二次検査：候補銘柄のみ1分足を並列取得し、単一バーの売買代金を検査する
            # ------------------------------
            results = []
            with ThreadPoolExecutor(
                max_workers=min(BLOCK_FETCH_MAX_WORKERS, len(candidates))
            ) as executor:
                futures = {
                    executor.submit(
                        _fetch_block_detection, c["コード"], target_date, threshold
                    ): c
                    for c in candidates
                }
                for future in as_completed(futures):
                    cand = futures[future]
                    try:
                        detection = future.result()
                    except Exception:
                        detection = None

                    if detection is None:
                        continue  # 該当バーなし（超大口の兆候なし）＝結果に含めない

                    results.append({
                        "コード": cand["コード"],
                        "銘柄名": cand["銘柄名"],
                        "日次売買代金": round(cand["日次売買代金"], 0),
                        **detection,
                    })

            results.sort(key=lambda x: x["最大売買代金"], reverse=True)
            return {"status": "ok", "data": results}

        except Exception as e:
            return {"error": "block screening failed", "detail": str(e)}

    else:
        return {"error": "invalid mode"}

# ============================
# /chart（週足・月足は日足から生成）
# ============================
@app.get("/chart")
def chart(ticker: str, timeframe: str = "1d"):
    try:
        symbol = f"{ticker}.T"

        # 日足を長期間取得
        df = yf.download(symbol, period="6000d", interval="1d", progress=False)
        if df.empty:
            return {"error": "no data"}

        if isinstance(df.columns, pd.MultiIndex):
            try:
                df = df.xs(symbol, level=1, axis=1)
            except Exception:
                pass

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
            "status": "ok",
            "Open": df_out["Open"].to_dict(),
            "High": df_out["High"].to_dict(),
            "Low": df_out["Low"].to_dict(),
            "Close": df_out["Close"].to_dict(),
            "Volume": df_out["Volume"].to_dict(),
        }

    except Exception as e:
        return {"error": "chart failed", "detail": str(e)}
