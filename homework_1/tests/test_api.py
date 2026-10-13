"""Проверка HTTP-слоя: формат запроса и ответа, /health, обработка плохого ввода."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(main.app) as c:   # with — чтобы отработал lifespan (индекс законов)
        yield c


def test_health(client):
    assert client.get("/health").json() == {"status": "healthy"}


def test_detect_example_from_task(client):
    r = client.post("/detect", json={"text": "пп. 1, 2, 3 п. 2 ст. 3 НК РФ"})
    assert r.status_code == 200
    links = r.json()["links"]
    assert links == [{"law_id": 15, "article": "3", "point_article": "2", "subpoint_article": s}
                     for s in "123"]


def test_response_has_all_four_fields(client):
    (link,) = client.post("/detect", json={"text": "ст. 5 УК РФ"}).json()["links"]
    assert set(link) == {"law_id", "article", "point_article", "subpoint_article"}
    assert link["point_article"] is None


def test_empty_and_no_links(client):
    assert client.post("/detect", json={"text": ""}).json() == {"links": []}
    assert client.post("/detect", json={"text": "Просто текст."}).json() == {"links": []}


def test_unknown_law_is_not_returned(client):
    assert client.post("/detect", json={"text": "ст. 17 Конституции Российской Федерации"}).json() == {"links": []}


def test_missing_text_is_422(client):
    assert client.post("/detect", json={}).status_code == 422
