"""
Поиск ссылок на нормативные акты в тексте.
"""

from __future__ import annotations
import re
from typing import Optional
from contracts import RawLink

# Токены уровней. Слева от токена не должно быть буквы ("пп." не содержит "п."),
# после слова — тоже ("пунктуация" не является "пунктом").
_NOT_LETTER_BEFORE = r"(?<![а-яёa-z])"
_WORD_END = r"(?![а-яё])"

ARTICLE_TOKENS = (
    rf"ст\.?{_WORD_END}"
    rf"|стат(?:ья|ьи|ье|ью|ьей|ьёй|ьям|ьями|ьях|ей){_WORD_END}"
)
POINT_TOKENS = (
    r"п\."
    r"|ч\."
    rf"|пункт(?:а|у|ом|е|ы|ов|ам|ами|ах)?{_WORD_END}"
    rf"|част(?:ь|и|ью|ей|ям|ями|ях){_WORD_END}"
)
SUBPOINT_TOKENS = (
    rf"пп\.?{_WORD_END}"
    r"|п\.\s*п\."
    rf"|подпункт(?:а|у|ом|е|ы|ов|ам|ами|ах)?{_WORD_END}"
)
TYPO_POINT = rf"(?:пунт|пукт)[а-яё]*{_WORD_END}"  # "пунта", "пукте"

#   - номер: 12, 4.6, 43.2-6, 30.1
#   - список: "1, 2, 3", "5 и 6", "3.345, 23"
VALUE_NUM = r"\d+(?:[.\-]\d+)*(?:\s*(?:,|и)\s*\d+(?:[.\-]\d+)*)*"
#   - буквы: "а", "а, б", "а, б и с". Буква должна стоять отдельно:
#     "пункте меню" — не пункт "м", "п. п." — не пункт "п"
_LETTER = rf"(?!(?:п|ч|ст|пп)\.)[а-яё]{_WORD_END}"
VALUE_LET = rf"{_LETTER}(?:\s*(?:,|и)\s*{_LETTER})*"
VALUE = rf"(?:{VALUE_NUM}|{VALUE_LET})"

# Статья: "<токен> <значение>"
RE_ARTICLE = re.compile(
    rf"{_NOT_LETTER_BEFORE}(?P<tok>{ARTICLE_TOKENS})\s*(?P<val>{VALUE})",
    re.IGNORECASE,
)

# Пункт / часть: "<токен> <значение>"
RE_POINT = re.compile(
    rf"{_NOT_LETTER_BEFORE}(?P<tok>{POINT_TOKENS}|{TYPO_POINT})\s*(?P<val>{VALUE})",
    re.IGNORECASE,
)

# Подпункт: "<токен> <значение>"
RE_SUBPOINT = re.compile(
    rf"{_NOT_LETTER_BEFORE}(?P<tok>{SUBPOINT_TOKENS})\s*(?P<val>{VALUE})",
    re.IGNORECASE,
)

# Название закона. Регистр важен только для аббревиатур, поэтому слова
# обёрнуты в (?i:...), а сам шаблон компилируется без IGNORECASE.

# Аббревиатура: минимум две заглавные буквы (УК, КоАП, АПК, ФЗ)
LAW_ABBR = r"[А-ЯЁ][А-Яа-яё]*[А-ЯЁ][А-Яа-яёЁ]*"

# Главное слово названия
LAW_HEAD = (
    r"(?i:(?:кодекс|закон|указ|приказ|устав|регламент)(?:а|у|ом|е|ы|ов)?"
    r"|конституци[яиюей]+"
    r"|(?:постановлени|положени)[еяюмих]+)(?![А-Яа-яёЁ])"
)

# Прилагательные перед главным словом: "Гражданского процессуального", "Федерального"
LAW_ADJ = r"(?i:[а-яё]+(?:ого|его|ому|ему|ый|ий|ой|ым|им|ом|ем))(?![А-Яа-яёЁ])"

# РФ в разных написаниях
RF = r"(?:РФ|(?i:Российской\s+Федерации|России))(?![А-Яа-яёЁ])"

# Номер акта: "№20-456", "№ 152-ФЗ"
LAW_NUMBER = r"(?:№|N)\s*\d[\w/\-]*"

# Название в кавычках
QUOTED = r"(?:«[^»\n]{1,300}»|\"[^\"\n]{1,300}\"|“[^”\n]{1,300}”)"

_LAW_TAIL = rf"(?:\s+{RF})?(?:\s*{LAW_NUMBER})?(?:\s+{QUOTED})?"

RE_LAW = re.compile(
    rf"(?:"
    rf"(?:{LAW_ADJ}\s+){{0,4}}{LAW_HEAD}{_LAW_TAIL}"
    rf"|{LAW_ABBR}{_LAW_TAIL}"
    rf"|{QUOTED}"
    rf")"
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
RE_SEPARATOR_BETWEEN_LEVELS = re.compile(r"^[\s,;]*(?:(?:и|или|в)\s+)?[\s,;]*$", re.IGNORECASE)

def _is_glue(fragment: str) -> bool:
    """True, если фрагмент между якорями — просто разделитель."""
    return bool(RE_SEPARATOR_BETWEEN_LEVELS.match(fragment))

def _group_anchors(anchors: list[_Anchor], text: str) -> list[list[_Anchor]]:
    if not anchors:
        return []

    # "п. а ч. 2 ст. 105": пункт внутри части — это уровень подпункта
    for prev, cur in zip(anchors, anchors[1:]):
        if (prev.kind == cur.kind == "point"
                and text[prev.start].lower() == "п" and text[cur.start].lower() == "ч"
                and _is_glue(text[prev.end:cur.start])):
            prev.kind = "subpoint"

    groups: list[list[_Anchor]] = [[anchors[0]]]
    for prev, cur in zip(anchors, anchors[1:]):
        between = text[prev.end:cur.start]
        # внутри ссылки уровни идут по возрастанию: подпункт -> пункт -> статья;
        # "ст. 5, п. 2 ст. 7" — это уже две ссылки
        ascending = _KIND_RANK[cur.kind] > _KIND_RANK[prev.kind]
        if _is_glue(between) and ascending:
            groups[-1].append(cur)
        else:
            groups.append([cur])
    return groups

# Вырезание
RE_SPACES = re.compile(r"[ \t\u00a0]*")

def _extract_law_text(text: str, search_from: int) -> tuple[Optional[str], int]:
    """
    Возвращает (law_text, end_pos). Если закона нет — (None, search_from).
    Название должно идти сразу после последнего уровня ссылки.
    """
    law_start = RE_SPACES.match(text, search_from).end()
    m = RE_LAW.match(text, law_start)
    if not m:
        return None, search_from
    return m.group(0), m.end()

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

    # "ст. 5, ст. 7 и п. 2 ст. 9 ТК РФ": закон назван один раз в конце перечисления
    for cur, nxt in zip(reversed(rawlinks[:-1]), reversed(rawlinks[1:])):
        if cur.law_text is None and nxt.law_text and _is_glue(text[cur.end:nxt.start]):
            cur.law_text = nxt.law_text
    return rawlinks