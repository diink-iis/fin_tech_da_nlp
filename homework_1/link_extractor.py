"""
Поиск ссылок на нормативные акты в тексте.
"""

from __future__ import annotations
import re
from typing import Optional
from contracts import RawLink

# Токены уровней
ARTICLE_TOKENS = (
    r"ст\."              # ст.
    r"|статьи|статье|статью|статьёй|статьей|статья"
)
POINT_TOKENS = (
    r"п\."               # п.
    r"|пункта|пункте|пункту|пункт"
    r"|части|частью|часть|ч\."   
)
SUBPOINT_TOKENS = (
    r"пп\."              # пп.
    r"|подпункта|подпункте|подпункту|подпунктов|подпунктах|подпункт"
)
TYPO_POINT = r"пунта|пунт[ае]?|пукта|п\.\s*р"  # "пунта", "п. р"
TYPO_ARTICLE = r"стат[ьяи]|ст\.?\s*$"

#   - номер: 12, 4.6, 43.2-6, 30.1
#   - список: "1, 2, 3", "5 и 6", "3.345, 23"
VALUE_NUM = r"\d+(?:[.\-]\d+)*(?:\s*(?:,|и)\s*\d+(?:[.\-]\d+)*)*"
#   - буквы: "а", "а, б", "а, б и с"
VALUE_LET = r"[а-яё](?:\s*(?:,|и)\s*[а-яё])*"
VALUE = rf"(?:{VALUE_NUM}|{VALUE_LET})"

# Статья: "<токен> <значение>"
RE_ARTICLE = re.compile(
    rf"(?P<tok>{ARTICLE_TOKENS})\s*(?P<val>{VALUE})",
    re.IGNORECASE,
)

# Пункт / часть: "<токен> <значение>"
RE_POINT = re.compile(
    rf"(?P<tok>{POINT_TOKENS}|{TYPO_POINT})\s*(?P<val>{VALUE})",
    re.IGNORECASE,
)

# Подпункт: "<токен> <значение>"
RE_SUBPOINT = re.compile(
    rf"(?P<tok>{SUBPOINT_TOKENS})\s*(?P<val>{VALUE})",
    re.IGNORECASE,
)

# Аббревиатуры кодексов
LAW_ABBR = (
    r"УК|НК|ГК|КоАП|ГПК|УПК|ТК|ЖК|ЗК|БК|СК|КАС|АПК|ЛК|ВК|КВВТ|УАТ"
    r"|УК\s*РФ|НК\s*РФ|ГК\s*РФ|КоАП\s*РФ|УПК\s*РФ|ГПК\s*РФ"
)

# Слова-маркеры закона
LAW_WORDS = (
    r"кодекс[а-яё]*"
    r"|закон[а-яё]*"
    r"|конституци[яиюей]"
    r"|устав[а-яё]*"
    r"|постановлени[еяюи][а-яё]*"
    r"|указ[а-яё]*"
    r"|приказ[а-яё]*"
    r"|федеральн[а-яё]+"
    r"|фз|ф\.з\."
)

# РФ в разных падежах и сокращения
RF = r"РФ|Российской\s+Федерации|России"

# Кавычки
QUOTED = r"«[^»]{1,80}»|\"[^\"]{1,80}\"|'[^']{1,80}'"

RE_LAW = re.compile(
    rf"(?:"
    rf"(?P<abbr>{LAW_ABBR})(?:\s*{RF})?"
    rf"|(?P<word>{LAW_WORDS})(?:\s+(?:{RF}|{QUOTED}))?"
    rf"|{QUOTED}"
    rf")",
    re.IGNORECASE,
)

# Вспомогательные структуры
class _Anchor:
    """Найденный уровень ссылки."""
    __slots__ = ("kind", "value", "start", "end")

    def __init__(self, kind: str, value: str, start: int, end: int) -> None:
        self.kind = kind      
        self.value = value.strip()
        self.start = start
        self.end = end

    def __repr__(self) -> str: 
        return f"_Anchor({self.kind}={self.value!r}, {self.start}..{self.end})"


# Приоритет уровня: чем больше — тем «выше» уровень
_KIND_RANK = {"article": 3, "point": 2, "subpoint": 1}

# Поиск якорей
def _find_anchors(text: str) -> list[_Anchor]:
    anchors: list[_Anchor] = []

    for m in RE_SUBPOINT.finditer(text):
        anchors.append(_Anchor("subpoint", m.group("val"), m.start(), m.end()))

    for m in RE_POINT.finditer(text):
        anchors.append(_Anchor("point", m.group("val"), m.start(), m.end()))

    for m in RE_ARTICLE.finditer(text):
        anchors.append(_Anchor("article", m.group("val"), m.start(), m.end()))

    # убрать пересечения
    anchors.sort(key=lambda a: (a.start, -_KIND_RANK[a.kind]))

    result: list[_Anchor] = []
    last_end = -1
    for a in anchors:
        if a.start < last_end:
            continue
        result.append(a)
        last_end = a.end
    return result

# Группировка якорей в ссылки
# Между двумя якорями допускаем только разделители, чтобы считать их одной ссылкой
RE_SEPARATOR_BETWEEN_LEVELS = re.compile(r"^[\s,;]*(?:и\s+|или\s+)?[\s,;]*$", re.IGNORECASE)

def _is_glue(fragment: str) -> bool:
    """True, если фрагмент между якорями — просто разделитель."""
    return bool(RE_SEPARATOR_BETWEEN_LEVELS.match(fragment))

def _group_anchors(anchors: list[_Anchor], text: str) -> list[list[_Anchor]]:
    if not anchors:
        return []

    groups: list[list[_Anchor]] = [[anchors[0]]]
    for prev, cur in zip(anchors, anchors[1:]):
        between = text[prev.end:cur.start]
        if _is_glue(between):
            groups[-1].append(cur)
        else:
            groups.append([cur])
    return groups

# Вырезание
RE_LAW_STOP = re.compile(r"[.,;:!?()\[\]{}\n]")

# Мусорные хвосты, которые нельзя включать
TRAILING_NOISE = re.compile(
    r"\s+(?:и\s+далее|и\s+др\.?|и\s+т\.?\s*д\.?|и\s+т\.?\s*п\.?)\s*$",
    re.IGNORECASE,
)

# Сколько слов максимум может занимать law_text
MAX_LAW_WORDS = 8

def _extract_law_text(text: str, search_from: int) -> tuple[Optional[str], int]:
    """
    Возвращает (law_text, end_pos). Если закона нет — (None, search_from).
    """
    tail = text[search_from:]
    stop = RE_LAW_STOP.search(tail)
    window_end = search_from + (stop.start() if stop else len(tail))
    window = text[search_from:window_end]
    words = window.split()
    if len(words) > MAX_LAW_WORDS:
        cut = 0
        count = 0
        for i, ch in enumerate(window):
            if ch.isspace() or ch == " ":
                count += 1
                if count == MAX_LAW_WORDS:
                    cut = i
                    break
        window = window[:cut] if cut else window
        window_end = search_from + len(window)

    m = RE_LAW.search(window)
    if not m:
        return None, search_from
    law_start = search_from + m.start()
    law_end = search_from + m.end()
    between = text[search_from:law_start]
    if between.strip(" \t,;:-"):
        return None, search_from
    law_raw = text[law_start:law_end]
    law_raw = TRAILING_NOISE.sub("", law_raw).rstrip(" \t,;:-")

    return (law_raw or None), law_end

# Сборка RawLink

def _build_rawlink(group: list[_Anchor], text: str) -> RawLink:
    """Из группы якорей собрать один RawLink"""
    article: Optional[str] = None
    point: Optional[str] = None
    subpoint: Optional[str] = None

    for a in group:
        if a.kind == "article" and article is None:
            article = a.value
        elif a.kind == "point" and point is None:
            point = a.value
        elif a.kind == "subpoint" and subpoint is None:
            subpoint = a.value

    start = min(a.start for a in group)
    rightmost_end = max(a.end for a in group)
    law_text, law_end = _extract_law_text(text, rightmost_end)
    end = law_end if law_text else rightmost_end

    return RawLink(
        start=start,
        end=end,
        article=article,
        point=point,
        subpoint=subpoint,
        law_text=law_text,
    )

def extract_links(text: str) -> list[RawLink]:
    """
    Находит в тексте ссылки на нормативные акты и раскладывает их на уровни.
    """
    if not text:
        return []
    anchors = _find_anchors(text)
    if not anchors:
        return []
    groups = _group_anchors(anchors, text)
    rawlinks = [_build_rawlink(g, text) for g in groups]
    rawlinks.sort(key=lambda r: r.start)
    return rawlinks