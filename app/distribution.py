"""
売り抜け検出（/screening?mode=distribution）に関する純粋関数と定数。
外部通信・グローバル状態を持たない（app/bottom_entry.py と同じ位置付け）。

GitHub Raw の取得・日付の形式検証・除外市場フィルタ・時価総額の付与は main.py 側に置き、
パラメータの検証・結果要素の整形・並び替えのみを本モジュールに分離する。
検出ロジック自体は本モジュールには無く、scripts/lib/distribution_conditions.js が
Actions で事前計算する。main.py は他モードの関数名と衝突しないよう、
本モジュールの関数名には distribution を含める。
"""

# アーカイブ（meta.params.version）の最低バージョン。これ未満は画面条件の絞り込みに
# 必要な指標値が無いため「古い版」として扱う。
ARCHIVE_MIN_VERSION = 1

# 画面条件の既定値（scripts/lib/distribution_conditions.js の下限以上であること）
DEFAULT_MIN_VOL_RATIO = 2.0    # 出来高倍率（倍）の下限
DEFAULT_MIN_BODY_DROP = 3.0    # 実体下落率（％）の下限
DEFAULT_MAX_HIGH_GAP = 10.0    # 前日終値の直近高値からの乖離（％）の上限（高値圏）
DEFAULT_MIN_RISE = 20.0        # 直前上昇率（％）の下限
DEFAULT_MIN_MARKET_CAP = 0.0   # 時価総額（円）の下限。0＝指定なし（アーカイブには含まれず、main.py が絞り込む）
MAX_MIN_MARKET_CAP = 1e15

DISTRIBUTION_MAX_RESULTS = 100  # date_ranking の上位100件・symbol_search と同じ規模

# 画面から指定できる範囲の「反対側の限度」。もう一方の限度はアーカイブの meta.params から読む
# （閾値の定義を scripts/lib/distribution_conditions.js の1か所に保つため）。
MAX_MIN_VOL_RATIO = 20.0
MAX_MIN_BODY_DROP = 30.0
MIN_MAX_HIGH_GAP = 1.0
MAX_MIN_RISE = 200.0


def archive_outdated(archive_params):
    """アーカイブが古い版（画面条件の指標値を持たない）なら True。"""
    try:
        return int((archive_params or {}).get("version", 0)) < ARCHIVE_MIN_VERSION
    except (TypeError, ValueError):
        return True


def _parse_number(raw, default, lower, upper, label):
    """文字列を数値へ変換し範囲を検証する。戻り値: (値, エラー文字列 or None)。"""
    if raw is None or raw == "":
        return default, None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None, f"{label} は数値で指定してください。"
    if not (lower <= value <= upper):
        return None, f"{label} は {lower:g}〜{upper:g} の範囲で指定してください。"
    return value, None


def parse_distribution_filters(raw, archive_params):
    """
    画面のスクリーニング条件を検証して dict で返す。戻り値: (filters, エラー文字列 or None)。
    raw のキー: min_vol_ratio / min_body_drop / max_high_gap / min_rise / min_market_cap（いずれも文字列 or None）。
    FastAPI の float 型は "nan"・"inf" も受理するため、文字列で受けてここで検証する
    （範囲比較は nan で False になり、エラーとして返る）。
    指定できる範囲の片側はアーカイブの meta.params（生成時の緩い下限）から決める。
    """
    p = archive_params or {}
    vol_lower = float(p.get("minVolumeMult", 2.0))
    body_lower = float(p.get("minBodyPct", 0.03)) * 100
    gap_upper = float(p.get("nearHighTol", 0.15)) * 100
    rise_lower = float(p.get("minRise", 0.10)) * 100

    specs = [
        ("min_vol_ratio", DEFAULT_MIN_VOL_RATIO, vol_lower, MAX_MIN_VOL_RATIO, "出来高倍率"),
        ("min_body_drop", DEFAULT_MIN_BODY_DROP, body_lower, MAX_MIN_BODY_DROP, "実体下落率"),
        ("max_high_gap", DEFAULT_MAX_HIGH_GAP, MIN_MAX_HIGH_GAP, gap_upper, "高値圏の許容乖離"),
        ("min_rise", DEFAULT_MIN_RISE, rise_lower, MAX_MIN_RISE, "直前上昇率"),
        ("min_market_cap", DEFAULT_MIN_MARKET_CAP, 0.0, MAX_MIN_MARKET_CAP, "時価総額"),
    ]
    filters = {}
    for key, default, lower, upper, label in specs:
        value, error = _parse_number(raw.get(key), default, lower, upper, label)
        if error:
            return None, error
        filters[key] = value
    return filters, None


def match_distribution_filters(sig, filters):
    """アーカイブの1銘柄ぶん（sig）が画面条件を満たすか。"""
    try:
        return (
            sig["volRatio"] >= filters["min_vol_ratio"]
            and sig["bodyPct"] >= filters["min_body_drop"]
            and sig["highGapPct"] >= -filters["max_high_gap"]
            and sig["risePct"] >= filters["min_rise"]
        )
    except (KeyError, TypeError):
        return False


def _round2(value):
    """画面表示用に小数2桁へ丸める（アーカイブの指標値は境界判定のため小数4桁で保存している）。"""
    # + 0.0 は、丸めた結果が -0.0 になるとき JSON に -0.0 が出力されるのを避けるため
    return None if value is None else round(value, 2) + 0.0


def build_distribution_item(code, name, segment, market_cap, sig):
    """アーカイブの1銘柄ぶん（sig）を /screening の結果要素（日本語キー）へ変換する。"""
    return {
        "コード": code,
        "銘柄名": name,
        "市場区分": segment,
        "時価総額": market_cap,
        "始値": sig.get("open"),
        "終値": sig.get("close"),
        "前日比": sig.get("dropPct"),
        "実体下落率": _round2(sig.get("bodyPct")),
        "出来高倍率": _round2(sig.get("volRatio")),
        "売買代金": sig.get("turnover"),
        "直近高値": sig.get("highPrice"),
        "高値日": sig.get("highDate"),
        "高値からの乖離率": _round2(sig.get("highGapPct")),
        "直前上昇率": _round2(sig.get("risePct")),
    }


def sort_distribution_items(items):
    """出来高倍率の降順 → 売買代金の降順 → コード昇順。"""
    return sorted(items, key=lambda r: (-(r.get("出来高倍率") or 0), -(r.get("売買代金") or 0), str(r.get("コード"))))


def limit_distribution_items(items):
    """DISTRIBUTION_MAX_RESULTS で打ち切る。-> (items, truncated)"""
    return items[:DISTRIBUTION_MAX_RESULTS], len(items) > DISTRIBUTION_MAX_RESULTS
