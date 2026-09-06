from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import pandas as pd
import requests
import json
import base64
from io import BytesIO
import warnings
import re
import os
import time
from datetime import datetime, timedelta
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
# 復習ページ機能：しおり・メモの保存（2026-09 追加）
# ============================
# 保存先はフロントエンド（webapp-frontend）リポジトリの data/review_notes.json。
#
# 権限分離の方針（2026-09 更新）：
# 既存の GITHUB_TOKEN は batches リポジトリの読み取り専用（Raw取得・trees API）
# として使い続け、変更しない。復習ページ機能の書き込み（Contents APIでの
# コミット）には、webapp-frontend リポジトリへの Contents: Read and write
# 権限のみを持つ別トークンを新たに発行し、REVIEW_GITHUB_TOKEN として設定する。
# こうすることで、既存の読み取り専用トークンに書き込み権限を付与する必要が
# なくなり、万一 REVIEW_GITHUB_TOKEN が漏洩しても被害範囲を
# webapp-frontend リポジトリのみに限定できる（最小権限の原則）。
REVIEW_GITHUB_TOKEN = os.getenv("REVIEW_GITHUB_TOKEN")


def review_github_headers():
    """
    復習ページ機能（review_notes.json / review_chapters.json）専用の
    GitHub API 用ヘッダ。既存の github_headers()（GITHUB_TOKEN・batches用）
    とは意図的に分離している。
    """
    headers = {}
    if REVIEW_GITHUB_TOKEN:
        headers["Authorization"] = f"token {REVIEW_GITHUB_TOKEN}"
    return headers


REVIEW_API_SECRET = os.getenv("REVIEW_API_SECRET")

# NOTE: batches リポジトリ（BASE_URL）とは別のリポジトリ。
# オーナー名・ブランチ名は既知の GitHub Pages 配信URL
# （https://yt-f6d34a22-537c-e881-530f-f9e7a956a78b.github.io/webapp-frontend/）
# から推測したものであり、実際のデフォルトブランチ名が main と異なる場合は
# REVIEW_NOTES_REPO_BRANCH を実態に合わせて修正すること（推測箇所）。
REVIEW_NOTES_REPO_OWNER = "yt-f6d34a22-537c-e881-530f-f9e7a956a78b"
REVIEW_NOTES_REPO_NAME = "webapp-frontend"
REVIEW_NOTES_REPO_BRANCH = "main"
REVIEW_NOTES_PATH = "data/review_notes.json"

REVIEW_NOTES_CONTENTS_API = (
    f"https://api.github.com/repos/{REVIEW_NOTES_REPO_OWNER}/{REVIEW_NOTES_REPO_NAME}"
    f"/contents/{REVIEW_NOTES_PATH}"
)
REVIEW_NOTES_RAW_URL = (
    f"https://raw.githubusercontent.com/{REVIEW_NOTES_REPO_OWNER}/{REVIEW_NOTES_REPO_NAME}"
    f"/refs/heads/{REVIEW_NOTES_REPO_BRANCH}/{REVIEW_NOTES_PATH}"
)

REVIEW_NOTES_DEFAULT = {"bookmarks": [], "memos": {}}


def _require_review_secret(x_review_secret: str | None):
    """
    書き込み系エンドポイント（しおり登録・メモ保存）共通の簡易認可チェック。

    NOTE: フロントエンドはビルド工程を持たない静的サイト（GitHub Pages）のため、
    review.js に合言葉を直接書いてコミットすると、誰でもページのソースを見れば
    値が分かってしまう（＝実質無認可と同じになる）。そのため、この合言葉は
    ソースにハードコードせず、review.js 側で初回操作時に prompt() で入力させ
    ブラウザの localStorage にのみ保持する方式を前提とする（詳細は
    review.js の getReviewSecret() を参照）。あくまで「URLを知っているだけの
    第三者による誤操作・いたずら書き込み」を防ぐための最低限の措置であり、
    本格的な認証（ログイン等）の代替ではない点に留意すること。
    """
    if not REVIEW_API_SECRET:
        # サーバー側で未設定のまま公開されると無防備な書き込みエンドポイントに
        # なってしまうため、未設定時は機能ごと拒否する（安全側に倒す）
        raise HTTPException(status_code=503, detail="review write endpoint is not configured")
    if not x_review_secret or x_review_secret != REVIEW_API_SECRET:
        raise HTTPException(status_code=401, detail="invalid or missing X-Review-Secret header")


def _get_github_json_for_write(contents_api: str, branch: str, default: dict, headers: dict):
    """
    GitHub Contents API から、書き込み対象JSONの最新内容と sha を取得する汎用ヘルパー。
    - Raw URL（CDNキャッシュあり）ではなく Contents API を使うのは、更新の競合を
      避けるため sha が常に最新である必要があるため。
    - ファイルが存在しない場合（初回書き込み前）は 404 になるため、その場合は
      default のコピーと sha=None を返す（新規作成として扱う）。
    - headers は呼び出し元がどのトークン（github_headers() /
      review_github_headers() など）を使うかを決めて渡す。
    """
    resp = requests.get(contents_api, headers=headers, params={"ref": branch})
    if resp.status_code == 404:
        return json.loads(json.dumps(default)), None  # default を書き換えないよう deep copy
    resp.raise_for_status()
    payload = resp.json()
    content = json.loads(base64.b64decode(payload["content"]).decode("utf-8"))
    return content, payload["sha"]


def _put_github_json_file(contents_api: str, branch: str, content: dict, sha: str | None, message: str, headers: dict):
    """
    JSONコンテンツを GitHub Contents API 経由でコミットする汎用ヘルパー。
    sha が None（ファイル新規作成）の場合は sha を省略して送信する。
    headers は呼び出し元が指定したトークンのヘッダをそのまま使う。
    """
    body = {
        "message": message,
        "content": base64.b64encode(
            json.dumps(content, ensure_ascii=False, indent=2).encode("utf-8")
        ).decode("utf-8"),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha
    resp = requests.put(contents_api, headers=headers, json=body)
    resp.raise_for_status()
    return resp.json()


def _get_review_notes_for_write():
    return _get_github_json_for_write(
        REVIEW_NOTES_CONTENTS_API, REVIEW_NOTES_REPO_BRANCH, REVIEW_NOTES_DEFAULT,
        headers=review_github_headers(),
    )


def _put_review_notes_file(content: dict, sha: str | None, message: str):
    return _put_github_json_file(
        REVIEW_NOTES_CONTENTS_API, REVIEW_NOTES_REPO_BRANCH, content, sha, message,
        headers=review_github_headers(),
    )


class BookmarkRequest(BaseModel):
    chapter_id: str
    bookmarked: bool


# ============================
# 復習ページ機能：章コンテンツの管理（2026-09 追加）
# ============================
# review.js に直書きしていた章本文を data/review_chapters.json（webapp-frontend
# リポジトリ）に切り出し、review_notes.json と同じ Contents API の仕組みで
# 読み書きできるようにする。これにより、章の追加・編集をコードの修正なしに
# 行えるようになる（POST /review/chapter を叩くか、GitHub上で直接JSONを編集する）。
REVIEW_CHAPTERS_PATH = "data/review_chapters.json"

REVIEW_CHAPTERS_CONTENTS_API = (
    f"https://api.github.com/repos/{REVIEW_NOTES_REPO_OWNER}/{REVIEW_NOTES_REPO_NAME}"
    f"/contents/{REVIEW_CHAPTERS_PATH}"
)
REVIEW_CHAPTERS_RAW_URL = (
    f"https://raw.githubusercontent.com/{REVIEW_NOTES_REPO_OWNER}/{REVIEW_NOTES_REPO_NAME}"
    f"/refs/heads/{REVIEW_NOTES_REPO_BRANCH}/{REVIEW_CHAPTERS_PATH}"
)

# data/review_chapters.json がまだ存在しない場合（初回稼働時）の初期コンテンツ。
# 従来 review.js に直書きしていた4章分をそのまま初期値として引き継いでいる。
REVIEW_CHAPTERS_DEFAULT = {
    "chapters": [
        {
            "id": "candlestick-basics",
            "part": "第1部　値動きを読む基礎",
            "title": "ローソク足とは何を表すか",
            "readMinutes": 3,
            "bodyHtml": (
                "<p class=\"lead\">ローソク足は、ある期間（日足なら1日）の始値・高値・安値・終値の"
                "4つの値を1本にまとめて表したものです。実体の色で「上がって終わったか、下がって終わったか」"
                "が一目で分かり、上下に伸びるヒゲで期間中にどこまで値が動いたかが分かります。</p>"
                "<p>実体が長いほど、その期間の始値と終値の差が大きい＝方向感のある値動きだったことを示します。"
                "逆に実体が小さいほど、始値付近で終わった＝迷いのある値動きだったと解釈できます。</p>"
                "<p>次の章では、実体に対してヒゲが極端に長い形（上ヒゲ・下ヒゲ）が何を示すのかを見ていきます。</p>"
            ),
        },
        {
            "id": "shadow-meaning",
            "part": "第1部　値動きを読む基礎",
            "title": "上ヒゲ・下ヒゲの意味",
            "readMinutes": 4,
            "bodyHtml": (
                "<p class=\"lead\">上ヒゲが長いローソク足は、一度は買われたものの、その水準を維持できなかった"
                "足跡です。高値圏で出現すると、上昇の勢いが尽きかけているサインとして扱われることが多くあります。</p>"
                "<p>重要なのは、<mark>ヒゲの長さそのものではなく、実体との比率</mark>です。実体が小さく上ヒゲだけが"
                "極端に長い形は、買い方が高値で売り方に押し返された結果であり、需給が転換しつつある可能性を示します。</p>"
                "<div class=\"callout\">この考え方は、スクリーニング条件の<strong>「出来高×上髭」</strong>および"
                "<strong>「上髭実体比以上」</strong>のしきい値設定に対応しています。"
                "<a href=\"index.html\">この条件でスクリーニングする →</a></div>"
                "<p>ただし、上ヒゲ単体で判断するのは危険です。出来高を伴わない上ヒゲは、単なる薄商いの振れであることも"
                "多く、次の章で扱う出来高とあわせて確認する必要があります。</p>"
            ),
        },
        {
            "id": "volume-meaning",
            "part": "第1部　値動きを読む基礎",
            "title": "出来高が語ること",
            "readMinutes": 4,
            "bodyHtml": (
                "<p class=\"lead\">出来高は「その日、どれだけの株数が売買されたか」を示します。値動きの大きさだけ"
                "でなく、その値動きにどれだけの参加者が関わっていたかを知る手がかりになります。</p>"
                "<p>前日と比べて出来高が急増している場合、何らかのニュースや材料をきっかけに新規の参加者が増えたと"
                "考えられます。上ヒゲ・下ヒゲと組み合わせることで、値動きの「勢い」と「信頼度」の両方を見ることが"
                "できます。</p>"
                "<div class=\"callout\">この考え方は、スクリーニング条件の<strong>「出来高倍率以上」</strong>の"
                "しきい値設定に対応しています。<a href=\"index.html\">この条件でスクリーニングする →</a></div>"
            ),
        },
        {
            "id": "heuristics-design",
            "part": "第2部　スクリーニングの考え方",
            "title": "経験則判定の設計思想",
            "readMinutes": 5,
            "bodyHtml": (
                "<p class=\"lead\">経験則判定（heuristics）は、複数のテクニカル指標を組み合わせて「上昇・下降"
                "どちらの可能性が高いか」をスコアとして算出する仕組みです。</p>"
                "<p>単一の指標だけで判断すると、ダマシ（一時的な逆行）に振り回されやすくなります。複数の指標が"
                "同じ方向を示しているときほど、その方向感の信頼度が高いと考えるのが基本的な設計思想です。</p>"
            ),
        },
    ]
}


def _get_review_chapters_for_write():
    return _get_github_json_for_write(
        REVIEW_CHAPTERS_CONTENTS_API, REVIEW_NOTES_REPO_BRANCH, REVIEW_CHAPTERS_DEFAULT,
        headers=review_github_headers(),
    )


def _put_review_chapters_file(content: dict, sha: str | None, message: str):
    return _put_github_json_file(
        REVIEW_CHAPTERS_CONTENTS_API, REVIEW_NOTES_REPO_BRANCH, content, sha, message,
        headers=review_github_headers(),
    )


def _cleanup_orphaned_review_data(chapter_id: str):
    """
    章削除時に、対応する review_notes.json 側の孤立データ（しおり・メモ）を
    あわせて削除する。

    章コンテンツ（review_chapters.json）としおり・メモ（review_notes.json）は
    別ファイルのため、章の削除コミットとは別にもう一度コミットが発生する
    （1回のAPI呼び出しで2回コミットされる点に留意）。

    このクリーンアップ自体が失敗しても章の削除そのものは取り消さない
    （呼び出し元 delete_review_chapter() は、このクリーンアップの成否に
    関わらず章の削除は成功として扱い、クリーンアップ結果は付随情報として
    レスポンスに含めるのみとする）。
    """
    try:
        content, sha = _get_review_notes_for_write()
        bookmarks = content.get("bookmarks", [])
        memos = content.get("memos", {})

        bookmark_removed = chapter_id in bookmarks
        memo_removed = chapter_id in memos

        if not bookmark_removed and not memo_removed:
            return {"bookmark_removed": False, "memo_removed": False}

        if bookmark_removed:
            content["bookmarks"] = [b for b in bookmarks if b != chapter_id]
        if memo_removed:
            memos.pop(chapter_id, None)
            content["memos"] = memos

        _put_review_notes_file(
            content,
            sha,
            message=f"chore(review): cleanup orphaned data for deleted chapter {chapter_id}",
        )
        return {"bookmark_removed": bookmark_removed, "memo_removed": memo_removed}
    except Exception as e:
        # クリーンアップの失敗は削除操作全体の失敗にはしない。
        # 呼び出し元のレスポンスに detail として含め、必要なら手動で再実行できるようにする。
        return {"error": str(e)}


class ChapterUpsertRequest(BaseModel):
    id: str
    part: str
    title: str
    readMinutes: int
    bodyHtml: str




class MemoRequest(BaseModel):
    chapter_id: str
    memo: str

# ============================
# 外部ファイル URL（Raw）
# ============================
BASE_URL = "https://raw.githubusercontent.com/yt-F6D34A22-537C-E881-530F-F9E7A956A78B/batches/refs/heads/main/data/"

EXCEL_URL = BASE_URL + "data_j.xlsx"
RAW_HEURISTICS_PREFIX = BASE_URL + "heuristics/"
RAW_OHLCV_PREFIX = BASE_URL + "ohlcv/"  # ohlcv_YYYYMMDD.json 形式（2026-07、data.json を置き換え）
MARKET_CAP_JSON_URL = BASE_URL + "market_cap.json"  # {コード: 発行済株式数} 形式（2026-07 追加）

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
market_cap_json = {}    # {コード: 発行済株式数}。2026-07 追加（時価総額列のため）
trading_dates_cache = []  # /trading_dates 用（3か月分の市場開場日。起動時に1回だけ取得）

# ------------------------------------------------------------------
# OHLCV（旧 data.json）: 固定N日ウィンドウを常駐させる方式を廃止し、
# リクエストされた日付分だけをオンデマンドで取得・キャッシュする
# （2026-07、OHLCV_WINDOW_DAYS 廃止に伴う変更。フロントエンド側の日付選択肢は
#   アーカイブの全期間から広く選べるようにする一方、バックエンドは実際に
#   扱うファイルだけに通信量を絞ることを目的とする）。
# ------------------------------------------------------------------
_ohlcv_dates_cache: list[str] = []        # 降順（新しい日付が先頭）。data/ohlcv/**/ohlcv_YYYYMMDD.json の一覧
_ohlcv_dates_cache_at: float = 0.0
OHLCV_DATES_CACHE_TTL_SEC = 300  # 5分。GitHub trees API のレート制限に配慮しつつ、
                                  # 当日分アーカイブの出現をほどよい遅延で反映する

_ohlcv_content_cache: dict[str, tuple[dict, float]] = {}  # date -> (data, fetched_at)
OHLCV_TODAY_CACHE_TTL_SEC = 300  # 5分。当日分は fetch.js により1日に複数回上書きされ得るため、
                                  # 恒久的に不変な過去日分とは別に短いTTLを設ける

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

def list_ohlcv_dates() -> list[str]:
    """
    GitHub trees API から data/ohlcv/**/ohlcv_YYYYMMDD.json を抽出し、
    降順（新しい日付が先頭）で返す。/heuristics_dates と同一パターン。
    """
    try:
        resp = requests.get(GIT_TREE_API, headers=github_headers())
        resp.raise_for_status()
        tree = resp.json().get("tree", [])
        dates = [
            m.group(1)
            for item in tree
            if (m := re.match(r"data/ohlcv/\d{6}/ohlcv_(\d{8})\.json$", item.get("path", "")))
        ]
        return sorted(dates, reverse=True)
    except Exception as e:
        print("Failed to list ohlcv dates:", e)
        return []

def get_ohlcv_dates() -> list[str]:
    """
    ohlcv アーカイブの日付一覧をTTLキャッシュ付きで返す（全期間・フル履歴）。
    /dates（プルダウン用。フロントエンドに広い選択肢を提供する）と、
    ratio/date_ranking モードの「対象日・直前営業日」解決の両方から使う。
    """
    global _ohlcv_dates_cache, _ohlcv_dates_cache_at
    if not _ohlcv_dates_cache or (time.time() - _ohlcv_dates_cache_at) > OHLCV_DATES_CACHE_TTL_SEC:
        dates = list_ohlcv_dates()
        if dates:
            _ohlcv_dates_cache = dates
            _ohlcv_dates_cache_at = time.time()
    return _ohlcv_dates_cache

def load_ohlcv_dates():
    """起動時に1回、日付一覧のキャッシュを温めておく（load_ticker_list() 等と同じ位置付け）。"""
    get_ohlcv_dates()

def fetch_ohlcv_file(date: str) -> dict:
    """1日分の ohlcv_YYYYMMDD.json（{コード: {o,h,l,c,v}}}）を取得する。"""
    try:
        resp = requests.get(f"{RAW_OHLCV_PREFIX}{date[:6]}/ohlcv_{date}.json")
        resp.raise_for_status()
        return json.loads(resp.text)
    except Exception as e:
        print(f"Failed to load ohlcv_{date}.json:", e)
        return {}

def get_ohlcv_for_date(date: str) -> dict:
    """
    指定日の ohlcv_YYYYMMDD.json をプロセス内キャッシュ付きで取得する。
    過去日分は恒久的に不変（fetch.js は当日以外を上書きしない）ため無期限にキャッシュしてよいが、
    当日（JST）分だけは1日に複数回上書きされ得るため OHLCV_TODAY_CACHE_TTL_SEC で再取得する。
    """
    today_jst = (datetime.utcnow() + timedelta(hours=9)).strftime("%Y%m%d")
    cached = _ohlcv_content_cache.get(date)
    if cached:
        data, fetched_at = cached
        if date != today_jst or (time.time() - fetched_at) < OHLCV_TODAY_CACHE_TTL_SEC:
            return data
    data = fetch_ohlcv_file(date)
    _ohlcv_content_cache[date] = (data, time.time())
    return data

def resolve_ratio_dates(target_date: str | None) -> tuple[str | None, str | None]:
    """
    ratio モード専用：target_date が有効なアーカイブ日ならその日と直前の営業日を、
    無効／未指定ならアーカイブ内の最新2営業日を返す
    （旧 data.json 版の「target_date が見つからない場合は最新2日にフォールバックする」
    挙動をそのまま踏襲する）。
    """
    dates = get_ohlcv_dates()
    if target_date and target_date in dates:
        idx = dates.index(target_date)
        if idx + 1 >= len(dates):
            return None, None
        return dates[idx], dates[idx + 1]
    if len(dates) < 2:
        return None, None
    return dates[0], dates[1]

def resolve_date_ranking_dates(target_date: str) -> tuple[str | None, str | None]:
    """
    date_ranking モード専用：target_date が有効なアーカイブ日ならその日と直前の営業日を返す。
    旧 data.json 版と同様、フォールバックは行わない（見つからなければ (None, None)）。
    """
    dates = get_ohlcv_dates()
    if target_date not in dates:
        return None, None
    idx = dates.index(target_date)
    if idx + 1 >= len(dates):
        return None, None
    return target_date, dates[idx + 1]

def load_market_cap():
    """
    銘柄ごとの発行済株式数（{コード: 株数}）を market_cap.json から読み込む。
    発行済株式数は株式分割・自己株買い等がなければ短期間では変化しないため、
    ohlcv アーカイブ（日次更新。2026-07、data.json から変更）とは別に、
    低頻度（週次を想定）で更新される市場データを想定している。生成は scripts/fetch_market_cap.py・
    .github/workflows/market_cap.yml を参照。
    時価総額そのもの（円建ての金額）ではなく株数を保持するのは、
    日々変動する終値と組み合わせて時価総額を都度計算するため
    （時価総額を直接保存すると、株価が動くたびに再生成が必要になり非効率）。
    """
    global market_cap_json
    try:
        resp = requests.get(MARKET_CAP_JSON_URL)
        resp.raise_for_status()
        market_cap_json = json.loads(resp.text)
    except Exception as e:
        print("Failed to load market_cap.json:", e)
        market_cap_json = {}

def calc_market_cap(code: str, price) -> float | None:
    """
    銘柄コードと株価から時価総額（円）を算出する。
    発行済株式数が market_cap_json に存在しない、または price が不正な場合は None を返す
    （フロント側は None を「-」として表示する）。
    """
    try:
        shares = market_cap_json.get(str(code))
        if not shares or price is None:
            return None
        return round(shares * price, 0)
    except Exception:
        return None

def load_trading_dates():
    """
    直近3か月の市場開場日一覧を取得し、trading_dates_cache に格納する。
    yfinance によるYahoo Financeへの外部通信は数秒〜十数秒かかることがあり、
    これをリクエスト処理のたびに行うと、Renderのコールドスタート（dyno起動）に
    かかる時間と合算してタイムアウトしてしまう不具合があった（2026-07 修正）。
    load_ticker_list() / load_ohlcv_dates() と同様に、起動時（モジュール読み込み時）に
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
load_ohlcv_dates()
load_market_cap()
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
    compare モードは ohlcv アーカイブ（ratio/date_ranking/heuristics/block が参照する
    日次OHLCV）を経由せず、常に yfinance から個別取得する設計を維持している
    （2026-07、data.json の恒久アーカイブ化後も本方針は変更していない）。
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
        dates = get_ohlcv_dates()
        if not dates:
            return {"error": "no ohlcv date data"}
        return {"status": "ok", "dates": dates}
    except Exception as e:
        return {"error": "failed to load dates", "detail": str(e)}

# ============================
# /trading_dates（compare モードの比較元日付用）
# ============================
@app.get("/trading_dates")
def get_trading_dates():
    """
    直近3か月の市場開場日一覧を返す。
    ohlcv アーカイブ（旧 data.json）は日付ごとに恒久保存されているが、
    compare モードの比較元日付セレクタには「市場が開いていた日」という
    暦カレンダー情報そのものが必要なため、
    起動時（モジュール読み込み時）に load_trading_dates() で1回だけ取得した
    trading_dates_cache を返す（2026-07 修正。以前はリクエスト毎に
    yfinance へ問い合わせていたが、Renderのコールドスタート時に
    起動遅延と外部通信時間が合算してタイムアウトする不具合があったため、
    /dates（get_ohlcv_dates()）と同じ「起動時に1回だけ取得してキャッシュする」
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

            today_key, prev_key = resolve_ratio_dates(target_date)
            if not today_key or not prev_key:
                return {"status": "ok", "data": []}

            today_data = get_ohlcv_for_date(today_key)
            prev_data = get_ohlcv_for_date(prev_key)

            for row in ticker_list:
                code = str(row["コード"])
                name = row["銘柄名"]
                symbol = code

                # 除外市場フィルタ（heuristics モードと同一ロジック）
                market = str(row.get("市場・商品区分", ""))
                if exclude_set and market in exclude_set:
                    continue

                today = today_data.get(symbol)
                prev = prev_data.get(symbol)

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
                        # 前日比（出来高）：(当日出来高 - 前日出来高) / 前日出来高 * 100
                        # vol_ratio_val（当日出来高 / 前日出来高）から導出できるが、
                        # 列見出し「出来高（前日出来高 / 前日比%）」の意味に合わせて
                        # 明示的に前日比（％）として算出する
                        vol_change_pct = (today_vol - prev_vol) / prev_vol * 100

                        results.append({
                            "コード": code,
                            "銘柄名": name,
                            "時価総額": calc_market_cap(code, close),
                            "終値": close,
                            "出来高": int(today_vol),
                            "前日出来高": int(prev_vol),
                            "出来高前日比": round(vol_change_pct, 2),
                            "売買代金": round(today_vol * close, 0),
                            "上髭": round(upper_shadow, 2),
                            "実体": round(real_body, 2),
                            "上髭実体比": round(shadow_ratio_val, 4),
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
            today_key, prev_key = resolve_date_ranking_dates(target_date)
            if not today_key or not prev_key:
                return {"status": "ok", "data": []}

            today_data = get_ohlcv_for_date(today_key)
            prev_data = get_ohlcv_for_date(prev_key)

            for row in ticker_list:
                code = str(row["コード"])
                name = row["銘柄名"]
                symbol = code

                today = today_data.get(symbol)
                prev = prev_data.get(symbol)

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
                        "時価総額": calc_market_cap(code, today_close),
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

                # 時価総額の算出には ohlcv_YYYYMMDD.json（ratio/date_ranking モードと共通の
                # 日次OHLCV。2026-07、data.json から変更）の target_date 時点の終値を用いる
                # （heuristics JSON 自体は終値を含まないため）
                day_data = get_ohlcv_for_date(target_date)
                price_for_cap = day_data.get(code_str, {}).get("c")

                array_data.append({
                    "コード":       code_str,
                    "銘柄名":       name,
                    "時価総額":     calc_market_cap(code_str, price_for_cap),
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
                        "時価総額": None,
                        "error": "終値データを取得できませんでした",
                    })
                    continue

                # 時価総額は比較元日付時点の株価（比較元終値）を基準に算出する。
                # コードごとに一定の値のため、対象日ループの外で1回だけ計算する。
                market_cap = calc_market_cap(code, from_close)

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
                                "時価総額": market_cap,
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
                            "時価総額": market_cap,
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
                        "時価総額": market_cap,
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
                    "時価総額": market_cap,
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
            # get_ohlcv_for_date(target_date) は ohlcv_YYYYMMDD.json 1ファイル分のみを
            # 取得する（未キャッシュの場合はGitHub Rawへの1回のHTTP取得が発生するが、
            # 全銘柄ぶんの1分足取得に比べれば軽量。2026-07、data.json 常駐方式から変更）。
            # 全銘柄（約4000銘柄）に対して毎回1分足を取得するのは非現実的
            # （Yahoo Finance のレート制限・応答時間の両面で）なため、
            # まずこの安価な処理で候補を上位 limit 件に絞り込む。
            # ------------------------------
            candidates = []
            day_data = get_ohlcv_for_date(target_date)
            for row in ticker_list:
                code = str(row["コード"])
                name = row["銘柄名"]

                market = str(row.get("市場・商品区分", ""))
                if exclude_set and market in exclude_set:
                    continue

                day = day_data.get(code)
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
                    "終値": close,
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
                        "時価総額": calc_market_cap(cand["コード"], cand["終値"]),
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

# ============================
# /review/notes（復習ページ：しおり・メモの読み込み）
# ============================
@app.get("/review/notes")
def get_review_notes():
    """
    しおり一覧・章ごとのメモをまとめて返す。
    閲覧は書き込みと異なり認可不要（既存の /dates 等の読み取り系エンドポイントと同じ扱い）。
    Raw URL（CDNキャッシュあり）から読むため、直前の書き込み直後は数分程度
    反映が遅れる場合がある。書き込み直後の画面反映は、POST の応答に含まれる
    最新状態をそのまま画面へ反映する形にし、本エンドポイントへの再取得には
    依存しないこと（review.js 側の実装方針。詳細はフロント側コメントを参照）。
    """
    try:
        resp = requests.get(REVIEW_NOTES_RAW_URL)
        if resp.status_code == 404:
            return {"status": "ok", **REVIEW_NOTES_DEFAULT}
        resp.raise_for_status()
        content = resp.json()
        return {"status": "ok", **content}
    except Exception as e:
        return {"error": "failed to load review notes", "detail": str(e)}

# ============================
# /review/bookmark（復習ページ：しおりの追加／解除）
# ============================
@app.post("/review/bookmark")
def set_review_bookmark(
    payload: BookmarkRequest,
    x_review_secret: str | None = Header(default=None, alias="X-Review-Secret"),
):
    """
    しおりの追加／解除。
    - bookmarked=true : chapter_id を bookmarks に追加（重複しても1件のみ保持）
    - bookmarked=false: chapter_id を bookmarks から削除
    """
    _require_review_secret(x_review_secret)
    try:
        content, sha = _get_review_notes_for_write()
        bookmarks = set(content.get("bookmarks", []))
        if payload.bookmarked:
            bookmarks.add(payload.chapter_id)
        else:
            bookmarks.discard(payload.chapter_id)
        content["bookmarks"] = sorted(bookmarks)

        _put_review_notes_file(
            content,
            sha,
            message=f"chore(review): update bookmark {payload.chapter_id}={payload.bookmarked}",
        )
        return {"status": "ok", "bookmarks": content["bookmarks"]}
    except HTTPException:
        raise
    except requests.HTTPError as e:
        return {"error": "github write failed", "detail": str(e)}
    except Exception as e:
        return {"error": "failed to update bookmark", "detail": str(e)}

# ============================
# /review/memo（復習ページ：章ごとのメモ保存）
# ============================
@app.post("/review/memo")
def set_review_memo(
    payload: MemoRequest,
    x_review_secret: str | None = Header(default=None, alias="X-Review-Secret"),
):
    """
    章ごとのメモを全文置き換えで保存する。
    空文字（前後空白のみ含む）で送信された場合は該当章のメモを削除する。
    """
    _require_review_secret(x_review_secret)
    try:
        content, sha = _get_review_notes_for_write()
        memos = content.get("memos", {})
        if payload.memo.strip() == "":
            memos.pop(payload.chapter_id, None)
        else:
            memos[payload.chapter_id] = payload.memo
        content["memos"] = memos

        _put_review_notes_file(
            content,
            sha,
            message=f"chore(review): update memo for {payload.chapter_id}",
        )
        return {"status": "ok", "memos": content["memos"]}
    except HTTPException:
        raise
    except requests.HTTPError as e:
        return {"error": "github write failed", "detail": str(e)}
    except Exception as e:
        return {"error": "failed to update memo", "detail": str(e)}

# ============================
# /review/chapters（復習ページ：章コンテンツの読み込み）
# ============================
@app.get("/review/chapters")
def get_review_chapters():
    """
    章コンテンツ（目次・本文）一覧を返す。
    Raw URL 経由で読むため、直前の書き込み直後は数分反映が遅れる場合がある。
    ファイルがまだ存在しない場合（初回稼働時）は REVIEW_CHAPTERS_DEFAULT を返す。
    """
    try:
        resp = requests.get(REVIEW_CHAPTERS_RAW_URL)
        if resp.status_code == 404:
            return {"status": "ok", **REVIEW_CHAPTERS_DEFAULT}
        resp.raise_for_status()
        content = resp.json()
        return {"status": "ok", **content}
    except Exception as e:
        return {"error": "failed to load review chapters", "detail": str(e)}

# ============================
# /review/chapter（復習ページ：章コンテンツの追加・更新・削除）
# ============================
@app.post("/review/chapter")
def upsert_review_chapter(
    payload: ChapterUpsertRequest,
    x_review_secret: str | None = Header(default=None, alias="X-Review-Secret"),
):
    """
    章コンテンツの追加・更新（upsert）。
    - payload.id が既存の章と一致する場合：その章を全項目で上書き（並び順は維持）
    - 一致しない場合：新しい章として末尾に追加
    章の id は一度使ったら変更しないこと（しおり・メモが id で紐付いているため、
    id を変更すると既存のしおり・メモが孤立する）。
    """
    _require_review_secret(x_review_secret)
    try:
        content, sha = _get_review_chapters_for_write()
        chapters = content.get("chapters", [])

        new_chapter = payload.model_dump()
        index = next((i for i, c in enumerate(chapters) if c.get("id") == payload.id), None)
        if index is not None:
            chapters[index] = new_chapter
        else:
            chapters.append(new_chapter)
        content["chapters"] = chapters

        _put_review_chapters_file(
            content,
            sha,
            message=f"chore(review): upsert chapter {payload.id}",
        )
        return {"status": "ok", "chapters": content["chapters"]}
    except HTTPException:
        raise
    except requests.HTTPError as e:
        return {"error": "github write failed", "detail": str(e)}
    except Exception as e:
        return {"error": "failed to upsert chapter", "detail": str(e)}


@app.delete("/review/chapter")
def delete_review_chapter(
    chapter_id: str,
    x_review_secret: str | None = Header(default=None, alias="X-Review-Secret"),
):
    """
    章コンテンツの削除。
    NOTE: 削除に成功すると、review_notes.json 側に残っている当該 chapter_id の
    しおり・メモも自動的にあわせて削除する（_cleanup_orphaned_review_data）。
    このクリーンアップが失敗した場合でも章そのものの削除は成功として扱い、
    レスポンスの "cleanup" にエラー内容を含める（手動で /review/bookmark・
    /review/memo を使って個別に整理することも可能）。
    """
    _require_review_secret(x_review_secret)
    try:
        content, sha = _get_review_chapters_for_write()
        chapters = content.get("chapters", [])
        remaining = [c for c in chapters if c.get("id") != chapter_id]
        if len(remaining) == len(chapters):
            return {"error": "chapter not found", "detail": chapter_id}
        content["chapters"] = remaining

        _put_review_chapters_file(
            content,
            sha,
            message=f"chore(review): delete chapter {chapter_id}",
        )

        cleanup_result = _cleanup_orphaned_review_data(chapter_id)

        return {
            "status": "ok",
            "chapters": content["chapters"],
            "cleanup": cleanup_result,
        }
    except HTTPException:
        raise
    except requests.HTTPError as e:
        return {"error": "github write failed", "detail": str(e)}
    except Exception as e:
        return {"error": "failed to delete chapter", "detail": str(e)}
