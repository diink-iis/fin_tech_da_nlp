from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Optional

import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel

import law_matcher
from law_matcher import find_law
from link_extractor import extract_links
from number_parser import expand

ALIASES_PATH = Path(__file__).resolve().with_name("law_aliases.json")


class LawLink(BaseModel):
    law_id: Optional[int] = None
    article: Optional[str] = None
    point_article: Optional[str] = None
    subpoint_article: Optional[str] = None


class LinksResponse(BaseModel):
    links: List[LawLink]


class TextRequest(BaseModel):
    text: str


def detect_links(text: str) -> List[LawLink]:
    """
    Конвейер: extract_links -> find_law -> expand.
    Ошибка на одной ссылке не роняет весь запрос: такая ссылка пропускается.
    """
    links: List[LawLink] = []
    try:
        raw_links = extract_links(text)
    except Exception as exc:
        print(f"extract_links: {exc!r}")
        return links

    for raw in raw_links:
        try:
            law_id = find_law(raw.law_text)
            links.extend(expand(raw, law_id))
        except Exception as exc:
            print(f"Ссылка {raw!r} пропущена: {exc!r}")
    return links


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: индекс алиасов строится один раз, а не на каждый запрос
    law_matcher.initialize(ALIASES_PATH)
    print("🚀 Сервис запускается...")
    yield
    # Shutdown
    print("🛑 Сервис завершается...")


app = FastAPI(
    title="Law Links Service",
    description="Cервис для выделения юридических ссылок из текста",
    version="1.0.0",
    lifespan=lifespan
)


@app.post("/detect")
def get_law_links(data: TextRequest) -> LinksResponse:
    """
    Принимает текст и возвращает список юридических ссылок
    """
    return LinksResponse(links=detect_links(data.text))


@app.get("/health")
async def health_check():
    """
    Проверка состояния сервиса
    """
    return {"status": "healthy"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8978)
