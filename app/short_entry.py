"""空売りエントリー検出（/screening?mode=short_entry）の純粋関数と定数。

外部通信・グローバル状態を持たない。GitHub Raw の取得などの I/O は main.py 側に置く
（app/bollinger.py・app/symbol_search.py と同じ位置付け）。
"""

# stage / phase の値は scripts/lib/short_entry_conditions.js の出力と一致させる
STAGE_FILTERS = {"all", "entry"}          # フロント #shortEntryStageSelect の option value と一致させる
DEFAULT_STAGE_FILTER = "all"
_PHASE_ORDER = {"entry": 0, "rebound": 1, "approach": 2}


def parse_stage_filter(value):
    """stage パラメータを検証する。(値, エラーメッセージ|None) を返す。省略時は all。"""
    if value is None or value == "":
        return DEFAULT_STAGE_FILTER, None
    if value not in STAGE_FILTERS:
        return None, f"stage must be one of {sorted(STAGE_FILTERS)}"
    return value, None


def build_item(code, name, segment, market_cap, sig):
    """検出結果1件を /screening の結果要素（日本語キー）へ変換する。"""
    return {
        "コード": code,
        "銘柄名": name,
        "市場区分": segment,
        "時価総額": market_cap,
        "ステージ": sig.get("stage"),
        "局面": sig.get("phase"),
        "反発回数": sig.get("reboundCount"),
        "終値": sig.get("close"),
        "MA50乖離率": sig.get("maGapPct"),
        "ネックライン": sig.get("neckline"),
        "ネックライン乖離率": sig.get("necklineGapPct"),
        "天井高値": sig.get("headPrice"),
        "天井日": sig.get("headDate"),
        "急落率": sig.get("dropPct"),
        "急落時出来高倍率": sig.get("dropVolRatio"),
    }


def sort_items(items):
    """エントリー → 反発中 → 接近の順。同一局面内はネックラインに近い（乖離率が小さい）順、コード昇順。"""
    return sorted(
        items,
        key=lambda x: (
            _PHASE_ORDER.get(x["局面"], 99),
            x["ネックライン乖離率"] if x["ネックライン乖離率"] is not None else float("inf"),
            x["コード"],
        ),
    )
