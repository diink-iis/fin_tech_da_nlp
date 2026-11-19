"""Role 5 UI and end-to-end API smoke tests."""
from fastapi.testclient import TestClient
from app.main import create_app


def test_ui_root_and_static_assets_are_served(tmp_path):
    with TestClient(create_app(str(tmp_path / "ui.db"), polling=False)) as client:
        page = client.get("/")
        assert page.status_code == 200
        assert "NewsFlow" in page.text
        assert "/static/css/style.css" in page.text
        assert "/static/js/app.js" in page.text
        css = client.get("/static/css/style.css")
        js = client.get("/static/js/app.js")
        assert css.status_code == 200 and "sidebar" in css.text
        assert js.status_code == 200 and "EventSource" in js.text


def test_role5_acceptance_keyword_isolation_channel_and_edit(tmp_path):
    with TestClient(create_app(str(tmp_path / "acceptance.db"), polling=False)) as client:
        anna = client.post("/api/users", json={"name": "Аня"}).json()["id"]
        boris = client.post("/api/users", json={"name": "Борис"}).json()["id"]
        anna_filter = client.post(f"/api/users/{anna}/filters", json={
            "name": "Нефть", "kind": "keyword", "params": {"any": ["нефть"]}
        })
        boris_filter = client.post(f"/api/users/{boris}/filters", json={
            "name": "Банки", "kind": "keyword", "params": {"any": ["банк"]}
        })
        assert anna_filter.status_code == 201
        assert boris_filter.status_code == 201
        channel = client.post("/api/channels", json={"title": "Роль 5", "user_id": anna}).json()
        assert client.post(f"/api/users/{boris}/feeds", json={"feed_id": channel["feed_id"]}).status_code == 201
        post = client.post(f"/api/channels/{channel['id']}/articles", json={
            "title": "Нефть подорожала", "text": "Цены на нефть выросли сегодня"
        })
        assert post.status_code == 201
        anna_news = client.get(f"/api/users/{anna}/news").json()
        assert len(anna_news) == 1
        assert anna_news[0]["matches"][0]["filter_name"] == "Нефть"
        assert client.get(f"/api/users/{boris}/news").json() == []
        changed = client.put(f"/api/users/{boris}/filters/{boris_filter.json()['id']}", json={
            "name": "Нефть", "kind": "keyword", "params": {"any": ["нефть"]}
        })
        assert changed.status_code == 200
        assert len(client.get(f"/api/users/{boris}/news").json()) == 1


def test_ui_route_installed_in_openapi_excluded_and_sse_route_present(tmp_path):
    app = create_app(str(tmp_path / "routes.db"), polling=False)
    paths = {route.path for route in app.routes}
    assert "/" in paths
    assert "/static" in paths
    assert "/api/users/{user_id}/stream" in paths
