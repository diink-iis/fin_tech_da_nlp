"""Role 5: server-rendered shell and static frontend routes."""
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

BASE = Path(__file__).resolve().parent


def install_ui_routes(app: FastAPI) -> None:
    """Attach the UI to the existing FastAPI app. Safe to call once per app."""
    if not any(getattr(route, "path", None) == "/static" for route in app.routes):
        app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")



    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def home():
        return (BASE / "templates" / "index.html").read_text(encoding="utf-8")
