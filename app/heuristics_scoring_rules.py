# =========================================================
# heuristicsスコア定義
#
# [2026-08 見直し] analysis/run_all.py（pooled検定 + daywise検定）による
# 実データ検証結果（data/heuristics 2025-12-30～2026-05-12の一部再生成分＋
# 過去分、計85日）を踏まえて重み付けを見直した。
#
# 判断基準：
#   - pooled検定・daywise検定の両方で、想定と逆方向の効果が有意に
#     確認できたルールは、方向（up/down）を反転した。
#   - pooled検定では有意だがdaywise検定で消える指標は、このアプリの
#     方法論（run_all.py guide()参照）に基づき「市場全体の地合いの
#     影響」の疑いがあるとみなし、重みを保守的に下げた。
#   - pooled・daywise両方で方向が一致し、効果量も大きかった指標は、
#     重みを維持または微増した。
#   - 今回の分析対象データはまだ全期間（2025-07-01～）の洗い替えが
#     完了しておらず、daywise検定のサンプル数も85日（推奨最小100日に
#     未達）と発展途上のため、以下の見直しは暫定的なものである。
#     洗い替え完了後、再度見直すことを推奨する。
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
            # [2026-08見直し] 4→5に微増。今回の検証で最も効果量の大きかった
            # 指標（daywise: +0.96%, p=2e-5、pooled: h5+0.84%【有望】,
            # h3+0.46%【有望】）。pooled・daywise両方で方向一致・高い有意性。
            "down": {"down": 5},
            "up":   {"up": 5},
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
    # [2026-08見直し] up→downへ反転。pooled検定（h3:-0.09%【留意】,
    # h5:-0.11%【留意】）・daywise検定（-0.22%, p=0.019, 【留意】）の
    # 両方で、想定（収束後の上昇）と逆に下落方向の効果が確認された。
    # =========================================================
    "TECH_MA_CONGESTION": {
        "type": "bool",
        "true": {"down": 2},  # 重みも3→2に下げた（他の高確度ルールほどの一貫性はないため）
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
    # [2026-08見直し] 重みを5→2に引き下げ。TECH_KAHANSHINはpooled検定で
    # 負方向（h3:-0.34%【有望】）が出ているが、より信頼性が高いとされる
    # daywise検定ではほぼ無相関（p=0.71）で、方向の根拠が弱いまま最高
    # ランクの重みが付いていた。TECH_GYAKU_KAHANSHINもhorizonごとに
    # 符号が不安定（h1:-0.31%【有望】, h3/h5:+方向）。方向自体は反転
    # せず（根拠不十分）、重みのみ保守的に下げた。
    # =========================================================
    "TECH_KAHANSHIN": {
        "type": "bool",
        "true": {"up": 2},
    },
    "TECH_GYAKU_KAHANSHIN": {
        "type": "bool",
        "true": {"down": 2},
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
    # [2026-08見直し] TECH_HEAD_AND_SHOULDERSは2026-08にネックライン確認
    # ロジックを追加したばかりで、pooled検定では上昇方向の【有望】
    # （h3:+1.11%）が出ているが、これは現在の重み付け（down）と逆方向。
    # daywise検定では無相関（p=0.90）でサンプルも薄い（n=568〜575）ため、
    # 方向を反転させるだけの根拠はまだない。重みを保守的に下げ、
    # データ蓄積後に再検証すべき「要注目」項目として扱う。
    # TECH_DOUBLE_BOTTOMも同様（大底圏チェックを追加したばかりで
    # horizonごとに符号が不安定なため、重みを保守的に下げた）。
    # =========================================================
    "TECH_HEAD_AND_SHOULDERS": {
        "type": "bool",
        "true": {"down": 1},
    },
    "TECH_DOUBLE_BOTTOM": {
        "type": "bool",
        "true": {"up": 1},
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
        "true": {"up": 1},  # [2026-08見直し] 3→2→1相当。daywiseでhorizonごとに符号不安定なため保守的に引き下げ
    },
    "TECH_RED_BLUE_CROSS": {
        "type": "bool",
        "true": {"up": 4},  # [2026-08確認] pooled・daywise両方で上昇方向が一致・有意。重み維持
    },
    # [2026-08見直し] up→downへ反転。pooled検定で全horizon・高い有意性で
    # 「戻り待ち売り後」の後も株価が下落し続ける結果（h1:-0.35%, h3:-0.67%,
    # h5:-0.78%、いずれも【有望】）。daywise検定でも同方向・有意
    # （-0.77%, p=0.006）。今回の見直しの中で最も一貫性の高い反転根拠。
    "TECH_RETURN_SELL_END": {
        "type": "bool",
        "true": {"down": 3},
    },
    # [2026-08見直し] up→downへ反転。pooled検定で全horizon・高い有意性で
    # 下落継続（h1:-0.11%, h3:-0.30%, h5:-0.43%）。daywise検定でも
    # 同方向・有意（-0.23%, p=0.0045）。「下降トレンドの終わり＝反転上昇」
    # という想定と実際のデータが逆だった。
    "TECH_DOWN_TREND_END": {
        "type": "bool",
        "true": {"down": 3},
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
    # [2026-08見直し] up→downへ反転（daily/weekly/monthlyすべて）。
    # pooled検定で3時間軸×3horizonのすべてで有意に下落方向
    # （-0.06%〜-0.42%）。原典資料（heuristics整理.xlsx「テクニカル」
    # シート26行目）の「ゾーンを終値で下へ割った場合、調整入りの
    # 目安となる」という定義とも整合する。当初の重み付けは、この
    # 「調整＝下落シグナル」という定義を取り違え、上昇方向に設定して
    # しまっていたと考えられる。
    # =========================================================
    "TECH_BB_ZONE_BREAK_DAILY": {
        "type": "bool",
        "true": {"down": 3},
    },
    "TECH_BB_ZONE_BREAK_WEEKLY": {
        "type": "bool",
        "true": {"down": 3},
    },
    "TECH_BB_ZONE_BREAK_MONTHLY": {
        "type": "bool",
        "true": {"down": 3},
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
