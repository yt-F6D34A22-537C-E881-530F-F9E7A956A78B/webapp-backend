"""銘柄検索（/screening?mode=symbol_search）に関する純粋関数。

外部通信・グローバル状態を持たない（app/bollinger.py と同じ位置付け）。
GitHub Raw の取得・キャッシュ等の I/O は main.py 側に置き、単体テストしやすくしている。
"""
import re
import unicodedata

MAX_CODES = 100        # codes に指定できる証券コードの最大件数
MAX_NAME_LENGTH = 50   # name の最大文字数（フロントの maxlength と同じ値）
MAX_RESULTS = 100      # 1回の検索で返す最大件数。コード指定の該当銘柄を先頭に並べてから切るため、
                       # MAX_CODES 以内のコード指定が銘柄名の該当で押し出されることはない

_CODE_PATTERN = re.compile(r"^[0-9A-Z]{4}$")   # /margin_info の MARGIN_INFO_CODE_PATTERN と同じ形式


def parse_codes(codes_param):
    """カンマ区切りの codes を (有効なコードのリスト, 形式不正トークンのリスト) へ分解する。

    NFKC 正規化により全角数字・全角カンマは半角になる。重複は除去し入力順を保つ。
    """
    valid, invalid = [], []
    for token in unicodedata.normalize("NFKC", codes_param or "").split(","):
        code = token.strip().upper()
        if not code:
            continue
        if _CODE_PATTERN.match(code):
            if code not in valid:
                valid.append(code)
        else:
            invalid.append(code)
    return valid, invalid


def normalize_name(text):
    """銘柄名比較用の正規化。表記ゆれ（全角半角・大小文字・空白・カタカナ/ひらがな）を吸収する。

    文字列以外（pd.read_excel 由来の欠損値 NaN 等）は空文字として扱う。
    """
    if not isinstance(text, str):
        return ""
    s = unicodedata.normalize("NFKC", text).casefold()
    s = "".join(s.split())   # 空白をすべて除去
    return "".join(
        chr(ord(c) - 0x60) if "\u30a1" <= c <= "\u30f6" else c   # カタカナ → ひらがな
        for c in s
    )
