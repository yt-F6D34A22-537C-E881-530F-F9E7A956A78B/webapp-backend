from heuristics_scoring_rules import HEURISTICS_SCORING_RULES

def calc_heuristics_score(tech: dict) -> dict:
    """
    1銘柄分のtechデータを受け取り、
    {"down": int, "up": int} を返す。
    """
    total_down = 0
    total_up   = 0

    for key, rule in HEURISTICS_SCORING_RULES.items():
        val   = tech.get(key)
        rtype = rule["type"]

        try:
            if rtype == "str_map":
                scores     = rule["map"].get(val, {})
                total_down += scores.get("down", 0)
                total_up   += scores.get("up",   0)

            elif rtype == "bool":
                if val is True:
                    scores = rule.get("true", {})
                elif val is False:
                    scores = rule.get("false", {})
                else:
                    continue
                total_down += scores.get("down", 0)
                total_up   += scores.get("up",   0)

            elif rtype == "int_val":
                if isinstance(val, (int, float)):
                    total_down += int(val) * rule.get("multiplier_down", 0)
                    total_up   += int(val) * rule.get("multiplier_up",   0)

            elif rtype == "int_threshold":
                # 値が threshold 以上のとき固定スコアを加点
                if isinstance(val, (int, float)) and val >= rule["threshold"]:
                    total_down += rule.get("down", 0)
                    total_up   += rule.get("up",   0)

            elif rtype == "dict_direction":
                if isinstance(val, dict):
                    direction  = val.get("direction")
                    count      = val.get("count", 0) or 0
                    scores     = rule["map"].get(direction, {})
                    total_down += scores.get("down", 0)
                    total_up   += scores.get("up",   0)
                    if count >= rule.get("count_threshold", 999):
                        bonus      = rule.get("count_bonus", {})
                        total_down += bonus.get("down", 0)
                        total_up   += bonus.get("up",   0)

            elif rtype == "dict_trycount":
                # tryCount が threshold 以上のとき固定スコアを加点
                if isinstance(val, dict):
                    try_count  = val.get("tryCount", 0) or 0
                    if try_count >= rule["threshold"]:
                        total_down += rule.get("down", 0)
                        total_up   += rule.get("up",   0)

        except Exception:
            continue

    return {"down": total_down, "up": total_up}
