from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from dataclasses import asdict
from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from app.db import NotFound, Repository
from app.contracts import Filter
from app.aggregator import FeedError, Poller, normalize_url, parse_feed, read_feed
from app.channels import ChannelError, ChannelStore, channel_feed_url
from app.engines.keyword import KeywordEngine
from app.engines.semantic import SemanticEngine, SemanticError, from_bytes, to_bytes

log = logging.getLogger(__name__)


class UserIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class FeedIn(BaseModel):
    url: str | None = None
    feed_id: int | None = None


class FilterIn(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    kind: Literal["keyword", "topic"]
    params: dict[str, Any] = Field(default_factory=dict)


class ChannelIn(BaseModel):
    title: str
    description: str = ""
    user_id: int | None = None


class PostIn(BaseModel):
    title: str
    text: str = ""
    link: str | None = None


def _validate_filter(data: FilterIn):
    p = data.params
    if data.kind == "keyword":
        if not any(p.get(k) for k in ("any", "all", "none")):
            raise HTTPException(422, "Укажите хотя бы один из any/all/none")
        for key in ("any", "all", "none"):
            v = p.get(key, [])
            if not isinstance(v, (str, list)) or (isinstance(v, list) and any(not isinstance(x, str) for x in v)):
                raise HTTPException(422, f"{key} должен быть строкой или списком строк")
        fields = p.get("fields", ["title", "summary"])
        if not isinstance(fields, list) or not fields or any(f not in ("title", "summary") for f in fields):
            raise HTTPException(422, "fields: выберите title и/или summary")
    else:
        desc = p.get("description", "")
        examples = p.get("examples", [])
        if not isinstance(desc, str) or not isinstance(examples, (str, list)) or (isinstance(examples,list) and any(not isinstance(x,str) for x in examples)):
            raise HTTPException(422, "description/examples: ожидаются строки")
        if not desc.strip() and not any(x.strip() for x in ([examples] if isinstance(examples,str) else examples)):
            raise HTTPException(422, "Укажите описание темы или примеры")
        try:
            threshold = float(p.get("threshold", 0.45))
        except (TypeError, ValueError):
            raise HTTPException(422, "threshold должен быть числом")
        if not -1 <= threshold <= 1:
            raise HTTPException(422, "threshold должен находиться в диапазоне [-1; 1]")
        if p.get("aggregate", "mean") not in ("mean", "max"):
            raise HTTPException(422, "aggregate: mean или max")


def _http_error(exc: Exception):
    if isinstance(exc, NotFound):
        raise HTTPException(404, str(exc)) from exc
    if isinstance(exc, (ChannelError, FeedError, ValueError)):
        raise HTTPException(400, str(exc)) from exc
    if isinstance(exc, sqlite3.IntegrityError):
        raise HTTPException(409, "Запись уже существует или нарушена целостность данных") from exc
    raise exc


def create_app(db_path: str | None = None, *, polling: bool = True,
               keyword_engine=None, semantic_engine=None) -> FastAPI:
    """Factory allows tests to use isolated SQLite and fake NLP engines."""
    repo = Repository(db_path)
    channels = ChannelStore(repo.path)  # same SQLite database, never a separate store
    keyword = keyword_engine if keyword_engine is not None else KeywordEngine()
    semantic = semantic_engine if semantic_engine is not None else SemanticEngine()
    poller = Poller(repo.list_feed_urls, repo.save_articles,
                    interval=float(os.getenv("RSS_POLL_SECONDS", "300")))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if polling: poller.start()
        try:
            yield
        finally:
            if polling: await poller.stop()

    app = FastAPI(title="RSS News Aggregator", version="1.0.0", lifespan=lifespan)
    app.state.repository = repo
    app.state.channels = channels
    app.state.poller = poller

    @app.get("/api/health")
    def health():
        return {"status": "ok", "db": "sqlite", "revision": repo.revision()}

    @app.get("/api/users")
    def list_users(): return repo.list_users()

    @app.post("/api/users", status_code=201)
    def create_user(payload: UserIn):
        try: return repo.create_user(payload.name)
        except Exception as exc: _http_error(exc)

    @app.get("/api/feeds")
    def list_feeds(): return repo.list_feeds()

    @app.get("/api/users/{user_id}/feeds")
    def user_feeds(user_id: int):
        try: return repo.user_feeds(user_id)
        except Exception as exc: _http_error(exc)

    @app.post("/api/users/{user_id}/feeds", status_code=201)
    async def subscribe_feed(user_id: int, payload: FeedIn):
        try:
            repo.require_user(user_id)
            if payload.feed_id is not None:
                feed = repo.get_feed(payload.feed_id)
            elif payload.url:
                url = normalize_url(payload.url)
                existing = next((f for f in repo.list_feeds() if f["url"] == url),None)
                if existing:
                    feed = existing
                else:
                    info = await asyncio.to_thread(read_feed,url)
                    feed = repo.add_feed(info.url,info.title)
                    repo.save_articles(feed["id"],info.articles)
            else:
                raise HTTPException(422, "Передайте url или feed_id")
            repo.subscribe(user_id,feed["id"])
            return feed
        except HTTPException: raise
        except Exception as exc: _http_error(exc)

    @app.delete("/api/users/{user_id}/feeds/{feed_id}")
    def unsubscribe_feed(user_id:int,feed_id:int):
        try:
            if not repo.unsubscribe(user_id,feed_id): raise NotFound("subscription not found")
            return {"ok":True}
        except Exception as exc:_http_error(exc)

    @app.get("/api/users/{user_id}/filters")
    def list_filters(user_id:int):
        try: return repo.list_filters(user_id)
        except Exception as exc: _http_error(exc)

    @app.post("/api/users/{user_id}/filters",status_code=201)
    def create_filter(user_id:int,payload:FilterIn):
        _validate_filter(payload)
        try: return repo.create_filter(user_id,payload.name.strip(),payload.kind,payload.params)
        except Exception as exc: _http_error(exc)

    @app.put("/api/users/{user_id}/filters/{filter_id}")
    def update_filter(user_id:int,filter_id:int,payload:FilterIn):
        _validate_filter(payload)
        try: return repo.update_filter(user_id,filter_id,payload.name.strip(),payload.kind,payload.params)
        except Exception as exc:_http_error(exc)

    @app.delete("/api/users/{user_id}/filters/{filter_id}")
    def delete_filter(user_id:int,filter_id:int):
        try:
            if not repo.delete_filter(user_id,filter_id):raise NotFound("filter not found")
            return {"ok":True}
        except Exception as exc:_http_error(exc)

    def filtered_news(user_id:int, limit:int, offset:int) -> list[dict]:
        filters = [Filter(id=f["id"],user_id=user_id,name=f["name"],kind=f["kind"],params=f["params"])
                   for f in repo.list_filters(user_id)]
        if not filters:return []
        result=[]
        # Offset/limit count MATCHED news, not articles before filtering.
        matched_count=0
        for position in range(0, 10_000, 200):
            articles=repo.news_candidates(user_id,200,position)
            if not articles: break
            for article in articles:
                reasons=[]
                for flt in filters:
                    try:
                        if flt.kind=="keyword":
                            reason=keyword.match(article,flt)
                        else:
                            # Reuse DB-stored vector after restart; model computation is lazy.
                            if hasattr(semantic,"preload") and hasattr(semantic,"model_name"):
                                saved=repo.get_vector(article.id,semantic.model_name)
                                if saved is not None:
                                    semantic.preload(article,from_bytes(saved))
                            reason=semantic.match(article,flt)
                            if hasattr(semantic,"article_vector") and hasattr(semantic,"model_name") and repo.get_vector(article.id,semantic.model_name) is None:
                                vector=semantic.article_vector(article)
                                if vector is not None: repo.put_vector(article.id,semantic.model_name,to_bytes(vector))
                    except (SemanticError,ValueError) as exc:
                        raise HTTPException(422,f"Фильтр {flt.name}: {exc}") from exc
                    if reason:reasons.append(asdict(reason))
                repo.save_matches(user_id,article.id,reasons)
                if reasons:
                    if matched_count>=offset:
                        d=asdict(article)
                        d["published_at"] = article.published_at.isoformat() if article.published_at else None
                        d["matches"]=reasons
                        d["matched_filters"]=reasons
                        result.append(d)
                    matched_count+=1
                    if len(result)>=limit:return result
            if len(articles)<200:break
        return result

    @app.get("/api/users/{user_id}/news")
    def news(user_id:int,limit:int=Query(50,ge=1,le=200),offset:int=Query(0,ge=0)):
        try:return filtered_news(user_id,limit,offset)
        except HTTPException:raise
        except Exception as exc:_http_error(exc)

    @app.get("/api/users/{user_id}/stream")
    async def stream(user_id:int,request:Request):
        try:repo.require_user(user_id)
        except Exception as exc:_http_error(exc)
        async def events():
            last=-1
            while not await request.is_disconnected():
                current=await asyncio.to_thread(repo.revision)
                if current!=last:
                    last=current
                    # Client fetches /news. No event-loop blocking NLP computation.
                    yield f"event: refresh\ndata: {json.dumps({'revision':current,'user_id':user_id})}\n\n"
                else:
                    yield ": keep-alive\n\n"
                await asyncio.sleep(2)
        return StreamingResponse(events(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

    @app.get("/api/channels")
    def list_channels():return [asdict(c) for c in channels.list_channels()]

    @app.post("/api/channels",status_code=201)
    def create_channel(payload:ChannelIn,request:Request):
        try:
            if payload.user_id is not None:repo.require_user(payload.user_id)
            ch=channels.create_channel(payload.title,payload.description)
            url=channel_feed_url(str(request.base_url),ch.id)
            feed=repo.add_feed(url,ch.title)
            if payload.user_id is not None:repo.subscribe(payload.user_id,feed["id"])
            return {**asdict(ch),"rss_url":url,"feed_id":feed["id"]}
        except Exception as exc:_http_error(exc)

    @app.post("/api/channels/{channel_id}/articles",status_code=201)
    def post_article(channel_id:int,payload:PostIn,request:Request):
        try:
            post=channels.post_article(channel_id,payload.title,payload.text,payload.link)
            url=channel_feed_url(str(request.base_url),channel_id)
            feed=repo.add_feed(url,channels.get_channel(channel_id).title)
            xml=channels.render_rss(channel_id,str(request.base_url))
            articles=parse_feed(xml,url,feed["id"]).articles
            inserted=repo.save_articles(feed["id"],articles)
            return {**asdict(post),"feed_id":feed["id"],"new_articles":inserted}
        except Exception as exc:_http_error(exc)

    @app.get("/rss/{channel_id}.xml")
    def rss(channel_id:int,request:Request):
        try:return Response(channels.render_rss(channel_id,str(request.base_url)),media_type="application/rss+xml; charset=utf-8")
        except ChannelError as exc:raise HTTPException(404,str(exc)) from exc

    @app.post("/api/refresh")
    async def refresh():
        results=await poller.refresh_now()
        return {"feeds":[asdict(r) for r in results],"new":sum(r.new for r in results),"errors":sum(bool(r.error) for r in results)}

    return app


app = create_app()
