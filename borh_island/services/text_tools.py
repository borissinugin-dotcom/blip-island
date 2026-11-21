"""Text transformation utilities: EN<->RU layout fix, case, translit, JSON, Base64, URL."""

from __future__ import annotations

import base64
import json
import re
from urllib.parse import quote, unquote

_EN_LAYOUT = "`qwertyuiop[]asdfghjkl;'zxcvbnm,.~QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>?@#$^&/"
_RU_LAYOUT = "ёйцукенгшщзхъфывапролджэячсмитьбюЁЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ,\"№;:?."

_EN_TO_RU = str.maketrans(_EN_LAYOUT, _RU_LAYOUT)
# Keep shared punctuation from breaking roundtrips when converting RU->EN
_RU_TO_EN_CHARS = "ёйцукенгшщзхъфывапролджэячсмитьбюЁЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭЯЧСМИТЬБЮ№"
_EN_FROM_RU_CHARS = "`qwertyuiop[]asdfghjkl;'zxcvbnm,.~QWERTYUIOP{}ASDFGHJKL:\"ZXCVBNM<>#"
_RU_TO_EN = str.maketrans(_RU_TO_EN_CHARS, _EN_FROM_RU_CHARS)

_CYR_TO_LAT: dict[str, str] = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}

_LAT_DIGRAPHS: list[tuple[str, str]] = [
    ("shch", "щ"), ("sch", "щ"), ("yo", "ё"), ("zh", "ж"), ("kh", "х"),
    ("ts", "ц"), ("ch", "ч"), ("sh", "ш"), ("yu", "ю"), ("ya", "я"),
    ("ye", "е"), ("iy", "ий"), ("yi", "ый"),
]
_LAT_SINGLES: dict[str, str] = {
    "a": "а", "b": "б", "c": "к", "d": "д", "e": "е", "f": "ф", "g": "г",
    "h": "х", "i": "и", "j": "й", "k": "к", "l": "л", "m": "м", "n": "н",
    "o": "о", "p": "п", "q": "к", "r": "р", "s": "с", "t": "т", "u": "у",
    "v": "в", "w": "в", "x": "кс", "y": "ы", "z": "з",
}


def fix_keyboard_layout(text: str) -> str:
    """Convert mistyped English<->Russian keyboard layout text automatically."""
    if not text:
        return text
    cyr = sum(1 for ch in text if "\u0400" <= ch <= "\u04FF")
    lat = sum(1 for ch in text if ("a" <= ch <= "z") or ("A" <= ch <= "Z"))
    if cyr > lat:
        return text.translate(_RU_TO_EN)
    return text.translate(_EN_TO_RU)


def to_upper(text: str) -> str:
    return text.upper()


def to_lower(text: str) -> str:
    return text.lower()


def to_title(text: str) -> str:
    return text.title()


def to_sentence(text: str) -> str:
    if not text:
        return text
    parts = re.split(r"([.!?]\s+)", text.strip())
    out: list[str] = []
    for idx, part in enumerate(parts):
        if idx % 2 == 0 and part:
            out.append(part[0].upper() + part[1:].lower())
        else:
            out.append(part)
    return "".join(out)


def invert_case(text: str) -> str:
    return text.swapcase()


def transliterate(text: str) -> str:
    """Transliterate Cyrillic -> Latin or Latin -> Cyrillic based on script majority."""
    if not text:
        return text
    cyr = sum(1 for ch in text if "\u0400" <= ch <= "\u04FF")
    lat = sum(1 for ch in text if ("a" <= ch <= "z") or ("A" <= ch <= "Z"))
    if cyr >= lat:
        out: list[str] = []
        for ch in text:
            low = ch.lower()
            if low in _CYR_TO_LAT:
                rep = _CYR_TO_LAT[low]
                if ch.isupper() and rep:
                    rep = rep[0].upper() + rep[1:]
                out.append(rep)
            else:
                out.append(ch)
        return "".join(out)

    # Latin -> Cyrillic
    res: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        matched = False
        rem_low = text[i:].lower()
        for src, dst in _LAT_DIGRAPHS:
            if rem_low.startswith(src):
                ch_out = dst.upper() if text[i].isupper() else dst
                res.append(ch_out)
                i += len(src)
                matched = True
                break
        if matched:
            continue
        ch = text[i]
        low = ch.lower()
        if low in _LAT_SINGLES:
            dst = _LAT_SINGLES[low]
            res.append(dst.upper() if ch.isupper() else dst)
        else:
            res.append(ch)
        i += 1
    return "".join(res)


def format_json(text: str, indent: int = 2) -> str:
    parsed = json.loads(text)
    return json.dumps(parsed, ensure_ascii=False, indent=indent)


def minify_json(text: str) -> str:
    parsed = json.loads(text)
    return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))


def base64_encode(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def base64_decode(text: str) -> str:
    raw = text.strip()
    padding = (-len(raw)) % 4
    raw += "=" * padding
    return base64.b64decode(raw.encode("ascii"), validate=False).decode("utf-8", errors="replace")


def url_encode(text: str) -> str:
    return quote(text, safe=":/?&=+#")


def url_decode(text: str) -> str:
    return unquote(text)


def text_stats(text: str) -> dict[str, int]:
    return {
        "chars": len(text),
        "chars_no_spaces": len(re.sub(r"\s+", "", text)),
        "words": len(re.findall(r"\S+", text)),
        "lines": 0 if not text else len(text.splitlines()),
    }
