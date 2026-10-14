"""Показ ошибок: какой текст, что ожидали, что получили, и чья это зона ответственности."""
from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

Link = Tuple[Optional[int], Optional[str], Optional[str], Optional[str]]

LAW_MATCHER = "law_matcher (участник 1)"
EXTRACTOR = "link_extractor (участник 2)"
PARSER = "number_parser (участник 3)"
EXTRACTOR_OR_PARSER = "link_extractor / number_parser (участник 2 или 3)"


@dataclass
class CaseErrors:
    case_id: str
    text: str
    missing: List[Link]    # ожидали, но не получили
    extra: List[Link]      # получили лишнее
    hints: List[str]       # куда смотреть


def _hint(link: Link, others: Set[Link], missing_side: bool) -> str:
    """Грубая эвристика: на каком уровне пара ошибок расходится."""
    for o in others:
        if o[1:] == link[1:] and o[0] != link[0]:
            return LAW_MATCHER
    for o in others:
        if o[0] == link[0] and o[1] == link[1]:
            return EXTRACTOR_OR_PARSER
    return EXTRACTOR + (": ссылка не найдена" if missing_side else ": лишняя ссылка")


def diff_case(case_id: str, text: str, expected: List[Link], predicted: List[Link]) -> Optional[CaseErrors]:
    e, p = set(expected), set(predicted)
    missing, extra = sorted(e - p, key=repr), sorted(p - e, key=repr)
    if not missing and not extra:
        return None
    hints: List[str] = []
    for m in missing:
        hints.append(f"нет {m}: {_hint(m, set(extra), True)}")
    for x in extra:
        hints.append(f"лишнее {x}: {_hint(x, set(missing), False)}")
    return CaseErrors(case_id, text, missing, extra, hints)


def collect_errors(cases: Dict[str, str], expected: Dict[str, List[Link]],
                   predicted: Dict[str, List[Link]]) -> List[CaseErrors]:
    out = []
    for case_id, text in cases.items():
        err = diff_case(case_id, text, expected[case_id], predicted.get(case_id, []))
        if err:
            out.append(err)
    return out


def format_errors(errors: List[CaseErrors], max_text: int = 120) -> str:
    lines: List[str] = []
    for err in errors:
        text = " ".join(err.text.split())
        if len(text) > max_text:
            text = text[:max_text] + "…"
        lines.append(f"[{err.case_id}] {text}")
        for m in err.missing:
            lines.append(f"    ожидали: {m}")
        for x in err.extra:
            lines.append(f"    получили: {x}")
        for h in err.hints:
            lines.append(f"    → {h}")
        lines.append("")
    return "\n".join(lines)
