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
    r"ст\.\s*ст\."
    rf"|ст\.?{_WORD_END}"
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
    r"|подп\."
    r"|п\.\s*п\.?(?![а-яё])"
    rf"|подпункт(?:а|у|ом|е|ы|ов|ам|ами|ах)?{_WORD_END}"
)
TYPO_POINT = rf"(?:пунт|пукт)[а-яё]*{_WORD_END}"  # "пунта", "пукте"

#   - номер: 12, 4.6, 43.2-6, 30.1
#   - список: "1, 2, 3", "5 и 6", "3.345, 23"
ORDINAL_WORDS = (r"(?i:перв|втор|трет|четвёрт|четверт|пят|шест|седьм|восьм|девят|десят)"
                 r"(?:ая|ой|ую|ые|ых|ого|ое|ому)")
_NUM = rf"(?:\d+(?:\.\d+)*(?:\s*[-–—]\s*\d+(?:\.\d+)*)?|{ORDINAL_WORDS})"
VALUE_NUM = rf"{_NUM}(?:\s*(?:,|и)\s*{_NUM})*"
#   - буквы: "а", "а, б", "а, б и с". Буква должна стоять отдельно:
#     "пункте меню" — не пункт "м", "п. п." — не пункт "п"; допустимы кавычки: п. «б»
_LETTER = rf"(?!(?:п|ч|ст|пп)\.)[\"«“„]?[а-яё][\"»”]?{_WORD_END}"
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
    r"(?i:(?:кодекс|закон|указ|приказ|устав|регламент|стандарт)(?:а|у|ом|е|ы|ов)?"
    r"|распоряжени[еяюмих]+"
    r"|конституци[яиюей]+"
    r"|(?:постановлени|положени)[еяюмих]+)(?![А-Яа-яёЁ])"
)

# Прилагательные перед главным словом: "Гражданского процессуального", "Федерального"
LAW_ADJ = r"(?i:[а-яё]+(?:-[а-яё]+)?(?:ого|его|ому|ему|ый|ий|ой|ым|им|ом|ем))(?![А-Яа-яёЁ])"

# РФ в разных написаниях
RF = r"(?i:РФ|Российской\s+Федерации|России)(?![А-Яа-яёЁ])"

# Номер акта: "№20-456", "№ 152-ФЗ"
LAW_NUMBER = r"(?:(?:№|N)\s*\d[\w/\-]*|\d+-(?:ФКЗ|ФЗ|КЗ|З))"

# Дата акта: "от 01.04.1996", "от 1 апреля 1996 года"
LAW_DATE = (r"от\s+\d{1,2}(?:\.\d{1,2}\.\d{2,4}"
            r"|\s+(?i:[а-яё]+)\s+\d{4}(?:\s+(?i:года|г\.))?)")

# Название в кавычках
QUOTED = r"(?:«[^»\n]{1,300}»|\"[^\"\n]{1,300}\"|“[^”\n]{1,300}”)"

ACT_SLASH_NUMBER = r"\d{1,3}/\d{2,4}"      # ПБУ 9/99, ФСБУ 28/2023
_CAPS_BEFORE_QUOTE = rf"(?:(?:\s+[А-ЯЁ]{{3,}}){{1,4}}(?=\s+{QUOTED}))?"   # ФСБУ ГОСУДАРСТВЕННЫХ ФИНАНСОВ "…"
_LAW_TAIL = (rf"(?:\s+{RF})?(?:\s+{LAW_DATE})?(?:\s*{LAW_NUMBER}|\s+{ACT_SLASH_NUMBER})?"
             rf"(?:\s+{LAW_DATE})?{_CAPS_BEFORE_QUOTE}(?:\s+{QUOTED})?")

# Известные сокращения кодексов принимаются и в нижнем регистре: "ук рф"
LAW_ABBR_LOWER = (r"(?i:(?:ук|нк|гк|тк|ск|жк|зк|бк|апк|гпк|упк|кас|коап|грк|вк|уик)(?![а-яё]))"
                  r"(?:\s+" + RF + r")?")

# "Кодекса административного судопроизводства", "Кодекса об административных правонарушениях"
LAW_POST = (r"(?:\s+(?i:Президента|Правительства)(?:\s+" + RF + r")?(?![а-яё])"
            r"|\s+(?i:бухгалтерского)\s+(?i:учёта|учета)(?:\s+(?i:государственных)\s+(?i:финансов))?(?![а-яё])"
            r"|(?:\s+(?i:[а-яё]+(?:ого|его))){1,2}\s+(?i:[а-яё]+[ая])(?![а-яё])"
            r"|(?:\s+(?i:Российской\s+Федерации|РФ))?\s+(?i:об?)\s+(?i:[а-яё]+ых)\s+(?i:[а-яё]+ях)(?![а-яё]))?")

# Стандарты бухучёта пишутся прописными: ПОЛОЖЕНИЕ ПО БУХГАЛТЕРСКОМУ УЧЕТУ ПБУ 7/98 "…"
ACCOUNTING_ACT = (rf"(?:ПОЛОЖЕНИЕ|ФЕДЕРАЛЬНЫЙ\s+СТАНДАРТ)(?:\s+[А-ЯЁ]{{2,}}|\s*\([^)\n]{{1,40}}\)"
                  rf"|\s+{ACT_SLASH_NUMBER})+(?:\s+{QUOTED})?")

RE_LAW = re.compile(
    rf"(?:"
    rf"{ACCOUNTING_ACT}|"
    rf"(?:{LAW_ADJ}\s+){{0,4}}{LAW_HEAD}{LAW_POST}{_LAW_TAIL}"
    rf"|{LAW_ABBR}{_LAW_TAIL}"
    rf"|{LAW_ABBR_LOWER}"
    rf"|{QUOTED}"
    rf")"
)

# После "ст. 24.5." закон следует только в виде аббревиатуры: "КоАП РФ", "УК"
RE_ABBR_ONLY = re.compile(rf"{LAW_ABBR}(?:\s+{RF})?")

# Вспомогательные структуры
class _Anchor:
    """Найденный уровень ссылки."""
    __slots__ = ("kind", "value", "start", "end", "tok")

    def __init__(self, kind: str, value: str, start: int, end: int, tok: str = "") -> None:
        self.kind = kind
        self.value = value.strip()
        self.start = start
        self.end = end
        self.tok = tok.lower()

    def __repr__(self) -> str: 
        return f"_Anchor({self.kind}={self.value!r}, {self.start}..{self.end})"


# Приоритет уровня: чем больше — тем «выше» уровень
_KIND_RANK = {"article": 3, "point": 2, "subpoint": 1}

# Поиск якорей
def _find_anchors(text: str) -> list[_Anchor]:
    anchors: list[_Anchor] = []

    for m in RE_SUBPOINT.finditer(text):
        anchors.append(_Anchor("subpoint", m.group("val"), m.start(), m.end(), m.group("tok")))

    for m in RE_POINT.finditer(text):
        anchors.append(_Anchor("point", m.group("val"), m.start(), m.end(), m.group("tok")))

    for m in RE_ARTICLE.finditer(text):
        anchors.append(_Anchor("article", m.group("val"), m.start(), m.end(), m.group("tok")))

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

# Разделитель без запятой и союзов: "ст. 105 ч. 2" — уровни идут сверху вниз
RE_PLAIN_SPACE = re.compile(r"^\s*$")

def _group_anchors(anchors: list[_Anchor], text: str) -> list[list[_Anchor]]:
    if not anchors:
        return []

    # "п. а ч. 2 ст. 105" и "ст. 105 ч. 2 п. а": пункт рядом с частью — подпункт
    for i, (prev, cur) in enumerate(zip(anchors, anchors[1:])):
        # "подпунктом г пункта 6 части 1": подпункт уже назван, "пункта 6" остаётся пунктом
        after_subpoint = i > 0 and anchors[i - 1].kind == "subpoint" and _is_glue(
            text[anchors[i - 1].end:prev.start])
        if prev.kind == cur.kind == "point" and _is_glue(text[prev.end:cur.start]) and not after_subpoint:
            if text[prev.start].lower() == "п" and text[cur.start].lower() == "ч":
                prev.kind = "subpoint"
            elif text[prev.start].lower() == "ч" and text[cur.start].lower() == "п":
                cur.kind = "subpoint"

    groups: list[list[_Anchor]] = [[anchors[0]]]
    direction: list[Optional[str]] = [None]   # "up": подпункт -> статья, "down": статья -> подпункт
    for prev, cur in zip(anchors, anchors[1:]):
        between = text[prev.end:cur.start]
        glue = _is_glue(between)
        rank_up = _KIND_RANK[cur.kind] > _KIND_RANK[prev.kind]
        # "ст. 5, п. 2 ст. 7" — две ссылки; "ст. 20.4 ч. 1" — одна, только через пробел
        down = (_KIND_RANK[cur.kind] < _KIND_RANK[prev.kind]
                and bool(RE_PLAIN_SPACE.match(between)))
        hybrid = (direction[-1] == "down" and cur.kind == "article"
                  and all(a.kind != "article" for a in groups[-1]) and glue)
        # "подпунктом г пункта 6 части 1": лишний уровень "часть" входит в ту же ссылку
        extra_part = (glue and prev.kind == cur.kind == "point" and direction[-1] == "up"
                      and any(a.kind == "subpoint" for a in groups[-1])
                      and text[cur.start].lower() == "ч")
        if extra_part:
            groups[-1].append(cur)
        elif glue and rank_up and direction[-1] in (None, "up") or hybrid:
            groups[-1].append(cur)
            direction[-1] = direction[-1] if hybrid else "up"
        elif down and direction[-1] in (None, "down"):
            groups[-1].append(cur)
            direction[-1] = "down"
        else:
            groups.append([cur])
            direction.append(None)
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
    if (not m and text[search_from:search_from + 1] == "."
            and re.search(r"\d\.\d+\Z", text[max(0, search_from - 12):search_from])):
        law_start = RE_SPACES.match(text, search_from + 1).end()
        m = RE_ABBR_ONLY.match(text, law_start)
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

    # "п.п. 3, 4 ст. 487" — пункты, если собственного пункта в ссылке нет
    if subpoint is not None and point is None:
        sub = next(a for a in group if a.kind == "subpoint")
        if re.fullmatch(r"п\.\s*п\.", sub.tok):
            point, subpoint = subpoint, None

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

    # номера внутри названия закона («…части четвертой Гражданского кодекса…») — не ссылки
    kept: list[RawLink] = []
    for r in rawlinks:
        if any(p.law_text and p.end - len(p.law_text) <= r.start < p.end for p in kept):
            continue
        kept.append(r)
    rawlinks = kept

    # "ч. 3, ч. 4 ст. 193 ТК РФ": ссылка без статьи берёт статью следующей за ней
    for cur, nxt in zip(rawlinks, rawlinks[1:]):
        if (cur.article is None and cur.point is not None and nxt.article is not None
                and nxt.point is not None and _is_glue(text[cur.end:nxt.start])):
            cur.article = nxt.article

    # "ст. 5, ст. 7 и п. 2 ст. 9 ТК РФ": закон назван один раз в конце перечисления
    for cur, nxt in zip(reversed(rawlinks[:-1]), reversed(rawlinks[1:])):
        if cur.law_text is None and nxt.law_text and _is_glue(text[cur.end:nxt.start]):
            cur.law_text = nxt.law_text
    return rawlinks