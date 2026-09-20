"""
ボリンジャーバンド関連の純粋関数（外部通信・グローバル状態を持たない）。

main.py の /screening?mode=margin_bb から利用する。
I/O（GitHub Raw 取得・キャッシュ）は main.py に置き、ここには計算ロジックのみを置くことで、
単体テストしやすく、将来 heuristics 等から再利用しやすい形にしている
（app/heuristics_scoring.py と同じ位置付け）。
"""
import math

# 期間（日足の本数）。フロント側 screening.js の MARGIN_BB_PERIOD と値を合わせること。
# チャート（chart-indicators.js の calcBB）の period=20 とも一致させている。
BB_PERIOD = 20

# 標準偏差の自由度補正。0＝母標準偏差（÷N）、1＝標本標準偏差（÷N-1）。
# chart-indicators.js の calcBB（variance /= period）と同じ母標準偏差（確認済み）。
BB_STD_DDOF = 0

# エリア定義：キー -> (下限σ〔以上〕, 上限σ〔未満。None なら上限なし〕)
# 「エリア」を追加する場合はこの dict に1行足すだけでよい
# （例: "band2to3": (2.0, 3.0)＝+2σ以上+3σ未満）。
# フロントの <select id="marginBbZoneSelect"> の option value と一致させること。
BB_ZONES = {
    "upper3": (3.0, None),  # +3σ以上
    "upper2": (2.0, None),  # +2σ以上
    "upper1": (1.0, None),  # +1σ以上
}
DEFAULT_BB_ZONE = "upper3"


def calc_bb_position(closes: list) -> float | None:
    """
    終値列（古い→新しい。長さ BB_PERIOD）から、最新終値のボリンジャーバンド上の位置
    （移動平均からの乖離を標準偏差で割った値＝σ単位）を返す。
    欠損（None）・0以下の値を含む場合、または標準偏差が0の場合は算出不能として None を返す。
    """
    if len(closes) != BB_PERIOD:
        return None
    if any(c is None or c <= 0 for c in closes):
        return None

    mean = sum(closes) / BB_PERIOD
    variance = sum((c - mean) ** 2 for c in closes) / (BB_PERIOD - BB_STD_DDOF)
    sigma = math.sqrt(variance)
    if sigma == 0:
        return None

    return (closes[-1] - mean) / sigma


def is_in_bb_zone(position: float, zone_key: str) -> bool:
    """position（σ単位）が BB_ZONES[zone_key] のエリアに含まれるかを返す。"""
    lower, upper = BB_ZONES[zone_key]
    return position >= lower and (upper is None or position < upper)


def calc_bb_area(position: float) -> int:
    """σ位置を整数のσ帯（例: +3.7σ → 3、+1.2σ → 1）に丸める。検索結果の並び替えキー用。"""
    return math.floor(position)
