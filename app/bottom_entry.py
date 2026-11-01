"""買い目線の下げ止まり検出（/screening?mode=bottom_entry）の純粋関数と定数。

外部通信・グローバル状態を持たない。GitHub Raw の取得などの I/O は main.py 側に置く
（app/short_entry.py と同じ位置付け）。main.py は short_entry の関数名（parse_stage_filter 等）を
そのまま import しているため、名前の衝突を避けるよう本モジュールの関数名には bottom を含める。
"""

import math

# stage / phase の値は scripts/lib/bottom_entry_conditions.js の出力と一致させる
BOTTOM_STAGE_FILTERS = {"all", "entry"}   # フロント #bottomEntryStageSelect の option value と一致させる
DEFAULT_BOTTOM_STAGE_FILTER = "all"
_PHASE_ORDER = {"entry": 0, "retest": 1, "approach": 2}

# --- 画面のスクリーニング条件（アーカイブの指標値での絞り込み） ---------------------------------
# アーカイブは「緩い下限」で生成され（meta.params）、各候補の指標値を含む。画面の条件は、その下限より
# 厳しい値だけを指定できる（下限より緩い値は、アーカイブに候補が無いためエラーとする）。
# 下限は meta.params から読むため、閾値の定義は scripts/lib/bottom_entry_conditions.js の1か所に保つ。
ARCHIVE_MIN_VERSION = 2                    # これ未満の版のアーカイブは指標値が無いため再生成が必要

# 省略時の値（フロントの各 select の既定値と一致させる）
BOTTOM_FILTER_DEFAULTS = {
    "min_drop": 15.0,          # 高値からの下落率の下限（％）
    "min_climax_ratio": 3.0,   # 投げ売り時の出来高倍率の下限（倍）
    "min_climax_drop": 10.0,   # 投げ売り直前の急落率の下限（％）
    "max_retest_vol": 50.0,    # 再テスト時の出来高（投げ売り日比）の上限（％）
    "bottom": "all",           # 対象の底：all（2番底・3番底）／2／3
}
_FILTER_UPPER = {"min_drop": 80.0, "min_climax_ratio": 20.0, "min_climax_drop": 50.0}   # 下限側は meta.params から決める
_MAX_RETEST_VOL_LOWER = 5.0
BOTTOM_VALUES = {"all", "2", "3"}


def parse_bottom_stage_filter(value):
    """stage パラメータを検証する。(値, エラーメッセージ|None) を返す。省略時は all。"""
    if value is None or value == "":
        return DEFAULT_BOTTOM_STAGE_FILTER, None
    if value not in BOTTOM_STAGE_FILTERS:
        return None, f"stage must be one of {sorted(BOTTOM_STAGE_FILTERS)}"
    return value, None


def build_bottom_item(code, name, segment, market_cap, sig):
    """検出結果1件を /screening の結果要素（日本語キー）へ変換する。"""
    return {
        "コード": code,
        "銘柄名": name,
        "市場区分": segment,
        "時価総額": market_cap,
        "ステージ": sig.get("stage"),
        "局面": sig.get("phase"),
        "番底": sig.get("bottomNo"),
        "終値": sig.get("close"),
        "第1底": sig.get("firstLow"),
        "第1底乖離率": sig.get("firstLowGapPct"),
        "確認安値": sig.get("retestLow"),                 # 接近（再テスト前）は None
        "高値からの下落率": sig.get("dropPct"),
        "投げ売り日": sig.get("climaxDate"),
        "投げ売り時出来高倍率": sig.get("climaxRatio"),
        "再テスト時出来高比": sig.get("retestVolRatio"),   # 接近（再テスト前）は None
        "ネックライン乖離率": sig.get("necklineGapPct"),
    }


def sort_bottom_items(items):
    """エントリー → 底確認中 → 接近の順。同一局面内は第1底に近い（乖離率が小さい）順、コード昇順。"""
    return sorted(
        items,
        key=lambda x: (
            _PHASE_ORDER.get(x["局面"], 99),
            x["第1底乖離率"] if x["第1底乖離率"] is not None else float("inf"),
            x["コード"],
        ),
    )


def archive_outdated(archive_params):
    """アーカイブが画面条件の絞り込みに必要な指標値を持たない古い版なら True。"""
    return (archive_params or {}).get("version", 0) < ARCHIVE_MIN_VERSION


def _archive_floors(archive_params):
    """アーカイブの生成時の下限（画面で指定できる最も緩い値）を、画面条件と同じ単位へ変換して返す。"""
    p = archive_params or {}
    return {
        "min_drop": p.get("minDropFromHigh", 0.15) * 100,
        "min_climax_ratio": p.get("volumeSpikeMult", 2.0),
        "min_climax_drop": p.get("climaxMinDrop", 0.05) * 100,
        "max_retest_vol": p.get("retestVolMaxRatio", 0.8) * 100,
    }


def parse_bottom_filters(raw, archive_params):
    """画面条件（文字列）を検証して数値へ変換する。(条件dict, エラーメッセージ|None) を返す。

    raw: {"min_drop", "min_climax_ratio", "min_climax_drop", "max_retest_vol", "bottom"}（各値は str か None。省略可）
    指定可能範囲：下限はアーカイブの生成時の下限（meta.params）、上限は本モジュールの定数。
    max_retest_vol のみ、アーカイブの下限が「上限」（これより大きい値は指定できない）になる。
    """
    floors = _archive_floors(archive_params)
    out = {}
    for key in ("min_drop", "min_climax_ratio", "min_climax_drop", "max_retest_vol"):
        text = raw.get(key)
        if text is None or text == "":
            default = BOTTOM_FILTER_DEFAULTS[key]
            # 既定値がアーカイブの下限（max_retest_vol は上限）の範囲外なら、範囲内へ寄せる
            out[key] = min(default, floors[key]) if key == "max_retest_vol" else max(default, floors[key])
            continue
        try:
            value = float(text)
        except (TypeError, ValueError):
            return None, f"{key} must be a number"
        if not math.isfinite(value):
            return None, f"{key} must be a finite number"
        if key == "max_retest_vol":
            lo, hi = _MAX_RETEST_VOL_LOWER, floors[key]
        else:
            lo, hi = floors[key], _FILTER_UPPER[key]
        if not (lo <= value <= hi):
            return None, f"{key} must be between {lo:g} and {hi:g}"
        out[key] = value
    bottom = raw.get("bottom")
    if bottom is None or bottom == "":
        bottom = BOTTOM_FILTER_DEFAULTS["bottom"]
    if bottom not in BOTTOM_VALUES:
        return None, f"bottom must be one of {sorted(BOTTOM_VALUES)}"
    out["bottom"] = bottom
    return out, None


def match_bottom_filters(sig, filters):
    """アーカイブの1銘柄ぶん（sig）が画面条件を満たすか。

    この絞り込みの結果は、同じ閾値で最初から検出し直した結果と一致する（scripts/lib/bottom_entry_conditions.js の
    ★の閾値は「通過／除外」の判定にだけ使われ、他の計算に影響しないため）。
    """
    eps = 1e-9
    drop, climax_ratio, climax_drop = sig.get("dropPct"), sig.get("climaxRatio"), sig.get("climaxDropPct")
    if drop is None or climax_ratio is None or climax_drop is None:
        return False                                              # 指標値が無い（判定できない）ものは対象外
    if -drop + eps < filters["min_drop"]:
        return False
    if climax_ratio + eps < filters["min_climax_ratio"]:
        return False
    if -climax_drop + eps < filters["min_climax_drop"]:
        return False
    retest_vol = sig.get("retestVolRatio")
    if retest_vol is not None and retest_vol * 100 > filters["max_retest_vol"] + eps:
        return False                                              # 再テスト前（None）は出来高の条件の対象外
    if filters["bottom"] != "all" and sig.get("bottomNo") != int(filters["bottom"]):
        return False
    return True
