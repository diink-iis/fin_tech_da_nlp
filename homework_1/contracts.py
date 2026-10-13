from dataclasses import dataclass
from typing import Optional

@dataclass
class RawLink:
    """Одна найденная в тексте ссылка, значения — сырые строки"""
    start: int
    end: int
    article: Optional[str] = None
    point: Optional[str] = None
    subpoint: Optional[str] = None
    law_text: Optional[str] = None

@dataclass
class LawLink:
    """Выход сервиса. Заполняется number_parser"""
    law_id: Optional[int]
    article: Optional[str]
    point_article: Optional[str]
    subpoint_article: Optional[str]