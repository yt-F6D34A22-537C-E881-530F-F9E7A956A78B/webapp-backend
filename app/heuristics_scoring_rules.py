# =========================================================
# heuristicsスコア定義
# =========================================================
HEURISTICS_SCORING_RULES: dict[str, dict] = {

    # =========================================================
    # 移動平均線の傾き
    # =========================================================
    "TECH_MA_SLOPE_DAILY": {
        "type": "str_map",
        "map": {
            "down": {"down": 5},
            "up":   {"up": 5},
        }
    },
    "TECH_MA_SLOPE_WEEKLY": {
        "type": "str_map",
        "map": {
            "down": {"down": 5},
            "up":   {"up": 5},
        }
    },
    "TECH_MA_SLOPE_MONTHLY": {
        "type": "str_map",
        "map": {
            "down": {"down": 5},
            "up":   {"up": 5},
        }
    },

    # =========================================================
    # 移動平均線の位置
    # =========================================================
    "TECH_MA_POSITION_DAILY": {
        "type": "str_map",
        "map": {
            "down": {"down": 4},
            "up":   {"up": 4},
        }
    },
    "TECH_MA_POSITION_WEEKLY": {
        "type": "str_map",
        "map": {
            "down": {"down": 4},
            "up":   {"up": 4},
        }
    },
    "TECH_MA_POSITION_MONTHLY": {
        "type": "str_map",
        "map": {
            "down": {"down": 4},
            "up":   {"up": 4},
        }
    },

    # =========================================================
    # パーフェクトオーダー
    # =========================================================
    "TECH_PERFECT_ORDER_DAILY": {
        "type": "bool",
        "true": {"up": 4},
    },
    "TECH_PERFECT_ORDER_WEEKLY": {
        "type": "bool",
        "true": {"up": 4},
    },
    "TECH_PERFECT_ORDER_MONTHLY": {
        "type": "bool",
        "true": {"up": 4},
    },

    # =========================================================
    # 逆パーフェクトオーダー
    # =========================================================
    "TECH_REVERSE_PERFECT_ORDER_DAILY": {
        "type": "bool",
        "true": {"down": 4},
    },
    "TECH_REVERSE_PERFECT_ORDER_WEEKLY": {
        "type": "bool",
        "true": {"down": 4},
    },
    "TECH_REVERSE_PERFECT_ORDER_MONTHLY": {
        "type": "bool",
        "true": {"down": 4},
    },

    # =========================================================
    # パーフェクトオーダー前夜
    # =========================================================
    "TECH_PRE_PERFECT_ORDER_DAILY": {
        "type": "bool",
        "true": {"up": 2},
    },
    "TECH_PRE_PERFECT_ORDER_WEEKLY": {
        "type": "bool",
        "true": {"up": 2},
    },
    "TECH_PRE_PERFECT_ORDER_MONTHLY": {
        "type": "bool",
        "true": {"up": 2},
    },

    # =========================================================
    # 逆パーフェクトオーダー前夜
    # =========================================================
    "TECH_PRE_REVERSE_PERFECT_ORDER_DAILY": {
        "type": "bool",
        "true": {"down": 2},
    },
    "TECH_PRE_REVERSE_PERFECT_ORDER_WEEKLY": {
        "type": "bool",
        "true": {"down": 2},
    },
    "TECH_PRE_REVERSE_PERFECT_ORDER_MONTHLY": {
        "type": "bool",
        "true": {"down": 2},
    },

    # =========================================================
    # 移動平均線の収束
    # =========================================================
    "TECH_MA_CONGESTION": {
        "type": "bool",
        "true": {"up": 3},
    },

    # =========================================================
    # 移動平均線の拡散
    # =========================================================
    "TECH_MA_SPREAD": {
        "type": "str_map",
        "map": {
            "down": {"down": 4},
            "up":   {"up": 4},
        }
    },

    # =========================================================
    # 100MAトレンド
    # =========================================================
    "TECH_MA100_TREND": {
        "type": "str_map",
        "map": {
            "down": {"down": 4},
            "up":   {"up": 4},
        }
    },

    # =========================================================
    # 下半身・逆下半身
    # =========================================================
    "TECH_KAHANSHIN": {
        "type": "bool",
        "true": {"up": 5},
    },
    "TECH_GYAKU_KAHANSHIN": {
        "type": "bool",
        "true": {"down": 5},
    },

    # =========================================================
    # 5MA更新
    # =========================================================
    "TECH_5MA_UPDATE": {
        "type": "str_map",
        "map": {
            "down": {"down": 3},
            "up":   {"up": 3},
        }
    },

    # =========================================================
    # 酒田五法
    # =========================================================
    "TECH_SAKATA_TRIPLE_TOP": {
        "type": "int_val",
        "multiplier_down": 2,
    },
    "TECH_SAKATA_TRIPLE_BOTTOM": {
        "type": "int_val",
        "multiplier_up": 2,
    },
    "TECH_SAKATA_SANKU_UP": {
        "type": "int_val",
        "multiplier_up": 2,
    },
    "TECH_SAKATA_SANKU_DOWN": {
        "type": "int_val",
        "multiplier_down": 2,
    },
    "TECH_SAKATA_SANPEI_UP": {
        "type": "int_val",
        "multiplier_up": 2,
    },
    "TECH_SAKATA_SANPEI_DOWN": {
        "type": "int_val",
        "multiplier_down": 2,
    },
    "TECH_SAKATA_SANPO_UP": {
        "type": "int_val",
        "multiplier_up": 2,
    },
    "TECH_SAKATA_SANPO_DOWN": {
        "type": "int_val",
        "multiplier_down": 2,
    },

    # =========================================================
    # パターン
    # =========================================================
    "TECH_HEAD_AND_SHOULDERS": {
        "type": "bool",
        "true": {"down": 3},
    },
    "TECH_DOUBLE_BOTTOM": {
        "type": "bool",
        "true": {"up": 3},
    },
    "TECH_NICHI_DAI": {
        "type": "bool",
        "true": {"up": 4},
    },
    "TECH_GYAKU_NICHI_DAI": {
        "type": "bool",
        "true": {"down": 4},
    },
    "TECH_IN_IN_HARAMI": {
        "type": "bool",
        "true": {"up": 2},
    },
    "TECH_RED_BLUE_CROSS": {
        "type": "bool",
        "true": {"up": 4},
    },
    "TECH_RETURN_SELL_END": {
        "type": "bool",
        "true": {"up": 1},
    },
    "TECH_DOWN_TREND_END": {
        "type": "bool",
        "true": {"up": 2},
    },
    "TECH_MOMIAI": {
        "type": "bool",
        "true": {"up": 5},
    },

    # =========================================================
    # 物別れ
    # =========================================================
    "TECH_MONOWAKARE": {
        "type": "str_map",
        "map": {
            "down": {"down": 3},
            "up":   {"up": 3},
        }
    },
    "TECH_MONOWAKARE_RED_BLUE_CROSS": {
        "type": "str_map",
        "map": {
            "down": {"down": 4},
            "up":   {"up": 4},
        }
    },

    # =========================================================
    # 9の法則
    # count_threshold: 9以上で反転シグナル（ボーナス加点）
    # count_bonus: 上昇9以上→下落加点、下降9以上→上昇加点（逆張り）
    # =========================================================
    "TECH_RULE9_DAILY": {
        "type": "dict_direction",
        "map": {
            "down": {"down": 3},
            "up":   {"up": 3},
        },
        "count_threshold": 9,
        "count_bonus": {
            "down": 2,
            "up":   2,
        },
    },
    "TECH_RULE9_WEEKLY": {
        "type": "dict_direction",
        "map": {
            "down": {"down": 3},
            "up":   {"up": 3},
        },
        "count_threshold": 9,
        "count_bonus": {
            "down": 2,
            "up":   2,
        },
    },

    # =========================================================
    # BBゾーンブレイク
    # =========================================================
    "TECH_BB_ZONE_BREAK_DAILY": {
        "type": "bool",
        "true": {"up": 3},
    },
    "TECH_BB_ZONE_BREAK_WEEKLY": {
        "type": "bool",
        "true": {"up": 3},
    },
    "TECH_BB_ZONE_BREAK_MONTHLY": {
        "type": "bool",
        "true": {"up": 3},
    },

    # =========================================================
    # ボックスレンジ
    # =========================================================
    "TECH_BOX_RANGE": {
        "type": "bool",
        # "true": {"up": 4},
    },

    # =========================================================
    # 過熱
    # =========================================================
    "TECH_OVERHEAT": {
        "type": "bool",
        "true": {"down": 5},
    },

    # =========================================================
    # グランビル
    # count_threshold: 第3法則（乖離過大）以上でボーナス加点
    # =========================================================
    "TECH_GRANVILLE": {
        "type": "dict_direction",
        "map": {
            "down": {"down": 3},
            "up":   {"up": 3},
        },
        "count_threshold": 3,
        "count_bonus": {
            "down": 1,
            "up":   1,
        },
    },

    # =========================================================
    # トレンドサイクル進行度
    # direction: "up"=上昇サイクル中, "down"=下降サイクル中
    # count: サイクル起点からの経過営業日数
    # =========================================================
    "TECH_CYCLE_PROGRESS": {
        "type": "dict_direction",
        "map": {
            "up":   {"up":   2},
            "down": {"down": 2},
        },
    },

    # =========================================================
    # 節目
    # tryCountが1以上のとき加点
    # TECH_FUSHIME_UP → up加点、TECH_FUSHIME_DOWN → down加点
    # =========================================================
    "TECH_FUSHIME_UP": {
        "type": "dict_trycount",
        "threshold": 1,
        "up": 3,
    },
    "TECH_FUSHIME_DOWN": {
        "type": "dict_trycount",
        "threshold": 1,
        "down": 3,
    },
}
