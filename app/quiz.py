"""
app/quiz.py（2026-10 新設）

値動きクイズ（/quiz/dates・/quiz/question・/quiz/answer）に関する
純粋関数と定数。外部通信・グローバル状態を持たない。
GitHub Raw の取得・キャッシュなどの I/O は main.py 側に置き、
入力の解析・絞り込み・判定のみを本モジュールに分離することで
単体テストしやすくしている（app/symbol_search.py・app/bollinger.py と同じ位置付け）。
"""
import math
from dataclasses import dataclass, field

from app.symbol_search import parse_codes, normalize_name, MAX_CODES

# 出題日の終値と、アーカイブ上でこの日数だけ後の日付の終値を比べて上下を判定する。
# フロントエンドへは /quiz/dates・/quiz/question のレスポンス（judge_days）で伝えるため、
# 値を変更してもフロントエンドの修正は不要。
QUIZ_JUDGE_DAYS = 5

# 日付未指定（ランダム日付）のとき、条件に合う銘柄が見つかるまで日付を引き直す最大回数。
# 1回の試行で最大2ファイル（出題日・判定日の ohlcv_YYYYMMDD.json）を取得するため、
# Render のレスポンス時間を考慮して小さく抑える。
QUIZ_MAX_ATTEMPTS = 5

# symbol（証券コードまたは銘柄名）の最大文字数。コードは最大 MAX_CODES 件のカンマ区切り。
QUIZ_MAX_SYMBOL_LENGTH = 200

# 時価総額帯（円）。キーが真実の情報源で、フロントエンドの <option value> と一致させること。
# (下限以上, 上限未満)。None は境界なし。
MARKET_CAP_BANDS = {
    "mega":  (1_000_000_000_000, None),                 # 超大型：1兆円以上
    "large": (300_000_000_000, 1_000_000_000_000),      # 大型：3,000億円〜1兆円未満
    "mid":   (100_000_000_000, 300_000_000_000),        # 中型：1,000億円〜3,000億円未満
    "small": (30_000_000_000, 100_000_000_000),         # 小型：300億円〜1,000億円未満
    "micro": (None, 30_000_000_000),                    # 超小型：300億円未満
}


@dataclass
class QuizFilters:
    """出題条件。None / 空は「未指定」（その条件では絞り込まない）。"""
    min_volume: float | None = None
    min_trade_value: float | None = None
    market_cap_band: str | None = None
    code_set: set = field(default_factory=set)
    name_query: str = ""

    @property
    def has_symbol_filter(self) -> bool:
        return bool(self.code_set) or bool(self.name_query)


def _is_positive_number(value) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    )


def validate_threshold(value, label: str) -> str | None:
    """
    出来高・売買金額の下限値を検証する。未指定（None）は有効。
    FastAPI の float は "nan" / "inf" も受理するため、有限かつ0以上であることを確認する。
    不正ならエラーメッセージ、問題なければ None。
    """
    if value is None:
        return None
    if not math.isfinite(value) or value < 0:
        return f"{label} は0以上の有限な数値で指定してください。"
    return None


def build_filters(min_volume, min_trade_value, market_cap_band, symbol) -> tuple[QuizFilters | None, str | None]:
    """
    クエリパラメータから QuizFilters を作る。不正な入力は (None, エラーメッセージ)。

    symbol は1つの入力欄に「証券コード（カンマ区切り可）」または「銘柄名（あいまい検索）」を
    受け取る。symbol_search モードと同じく、コードとして解釈できたトークンと銘柄名の
    あいまい一致を OR で扱う（例：「KDDI」は形式上 4 桁コードとしても有効だが、
    コード該当なしでも銘柄名で一致する）。形式不正のトークンは無視される（銘柄名検索の対象）。
    """
    for value, label in ((min_volume, "min_volume"), (min_trade_value, "min_trade_value")):
        message = validate_threshold(value, label)
        if message:
            return None, message

    if market_cap_band and market_cap_band not in MARKET_CAP_BANDS:
        return None, "market_cap_band が不正です。"

    code_set: set = set()
    name_query = ""
    if symbol and symbol.strip():
        if len(symbol) > QUIZ_MAX_SYMBOL_LENGTH:
            return None, f"symbol は {QUIZ_MAX_SYMBOL_LENGTH} 文字以内で指定してください。"
        code_list, _invalid = parse_codes(symbol)
        if len(code_list) > MAX_CODES:
            return None, f"証券コードは {MAX_CODES} 件以内で指定してください。"
        code_set = set(code_list)
        name_query = normalize_name(symbol)

    return QuizFilters(
        min_volume=min_volume,
        min_trade_value=min_trade_value,
        market_cap_band=market_cap_band or None,
        code_set=code_set,
        name_query=name_query,
    ), None


def market_cap_in_band(market_cap, band_key: str) -> bool:
    """時価総額（円）が帯に含まれるか。時価総額が不明（None）の銘柄は帯指定時は対象外。"""
    if not _is_positive_number(market_cap):
        return False
    lower, upper = MARKET_CAP_BANDS[band_key]
    if lower is not None and market_cap < lower:
        return False
    if upper is not None and market_cap >= upper:
        return False
    return True


def pick_candidates(ticker_rows, day_data: dict, shares_by_code: dict, filters: QuizFilters) -> list[dict]:
    """
    出題日の ohlcv（{コード: {o,h,l,c,v}}）から、条件に合う出題候補を返す。
    売買金額は 終値 × 出来高 で算出する（/screening の ratio モードの「売買代金」と同じ定義）。
    時価総額は 発行済株式数 × 出題日の終値（calc_market_cap と同じ定義）。
    """
    candidates = []
    for row in ticker_rows:
        code = str(row["コード"])

        if filters.has_symbol_filter:
            by_code = code in filters.code_set
            by_name = bool(filters.name_query) and filters.name_query in normalize_name(row["銘柄名"])
            if not (by_code or by_name):
                continue

        bar = day_data.get(code)
        if not bar:
            continue
        close, volume = bar.get("c"), bar.get("v")
        if not _is_positive_number(close) or not _is_positive_number(volume):
            continue

        trade_value = close * volume
        if filters.min_volume is not None and volume < filters.min_volume:
            continue
        if filters.min_trade_value is not None and trade_value < filters.min_trade_value:
            continue

        shares = shares_by_code.get(code)
        market_cap = round(shares * close, 0) if _is_positive_number(shares) else None
        if filters.market_cap_band and not market_cap_in_band(market_cap, filters.market_cap_band):
            continue

        candidates.append({
            "code": code,
            "name": str(row["銘柄名"]),
            "close": close,
            "volume": volume,
            "trade_value": trade_value,
            "market_cap": market_cap,
        })
    return candidates


def filter_answerable(candidates: list[dict], judge_data: dict) -> list[dict]:
    """判定日にも有効な終値がある銘柄だけに絞る（出題後に /quiz/answer が必ず判定できるようにする）。"""
    return [c for c in candidates if _is_positive_number((judge_data.get(c["code"]) or {}).get("c"))]


def format_date_key(date_key: str) -> str:
    """YYYYMMDD → YYYY-MM-DD（/chart の end_date・レスポンスの日付と同じ書式）。"""
    return f"{date_key[:4]}-{date_key[4:6]}-{date_key[6:8]}"


def judge_direction(base_close: float, judge_close: float) -> str:
    """出題日の終値に対する判定日の終値の方向。同値は "flat"。"""
    if judge_close > base_close:
        return "up"
    if judge_close < base_close:
        return "down"
    return "flat"


def calc_change_pct(base_close: float, judge_close: float) -> float:
    return round((judge_close - base_close) / base_close * 100, 2)


def evaluate_guess(direction: str, guess: str) -> bool | None:
    """正誤。同値（flat）は正解・不正解のどちらにもしない（None＝引き分け）。"""
    if direction == "flat":
        return None
    return direction == guess
