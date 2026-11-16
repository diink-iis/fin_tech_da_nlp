from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from app.contracts import Article, Filter

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
 id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feeds (
 id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT NOT NULL UNIQUE,
 title TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subscriptions (
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 feed_id INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
 PRIMARY KEY(user_id, feed_id)
);
CREATE TABLE IF NOT EXISTS filters (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 name TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('keyword', 'topic')),
 params TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS articles (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 feed_id INTEGER NOT NULL REFERENCES feeds(id) ON DELETE CASCADE,
 guid TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL,
 link TEXT NOT NULL DEFAULT '', published_at TEXT NOT NULL,
 UNIQUE(feed_id, guid)
);
CREATE INDEX IF NOT EXISTS idx_articles_feed ON articles(feed_id, published_at DESC);
CREATE TABLE IF NOT EXISTS matches (
 user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
 article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
 filter_id INTEGER NOT NULL REFERENCES filters(id) ON DELETE CASCADE,
 reason TEXT NOT NULL,
 PRIMARY KEY(user_id, article_id, filter_id)
);
CREATE TABLE IF NOT EXISTS article_vectors (
 article_id INTEGER PRIMARY KEY REFERENCES articles(id) ON DELETE CASCADE,
 model TEXT NOT NULL, vector BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS revisions (
 id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO revisions(id,version) VALUES(1,0);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class NotFound(LookupError):
    pass


class Repository:
    def __init__(self, path: str | Path | None = None):
        self.path = str(path or os.environ.get("RSS_DB_PATH", "data/news.db"))
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.executescript(SCHEMA)

    @contextmanager
    def connection(self):
        conn = sqlite3.connect(self.path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=20000")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _bump(conn):
        conn.execute("UPDATE revisions SET version=version+1 WHERE id=1")

    def revision(self) -> int:
        with self.connection() as conn:
            return conn.execute("SELECT version FROM revisions WHERE id=1").fetchone()[0]

    @staticmethod
    def _require(conn, table: str, id_: int):
        if table not in ("users", "feeds", "filters"):
            raise ValueError("invalid table")
        if conn.execute(f"SELECT 1 FROM {table} WHERE id=?", (id_,)).fetchone() is None:
            raise NotFound(f"{table}: {id_} not found")

    def create_user(self, name: str) -> dict:
        name = name.strip()
        if not name or len(name) > 100:
            raise ValueError("Имя пользователя должно содержать 1–100 символов")
        with self.connection() as conn:
            cur = conn.execute("INSERT INTO users(name,created_at) VALUES(?,?)", (name, utcnow()))
            self._bump(conn)
            return dict(conn.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone())

    def list_users(self) -> list[dict]:
        with self.connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM users ORDER BY id")]

    def require_user(self, user_id: int):
        with self.connection() as conn:
            self._require(conn, "users", user_id)

    def add_feed(self, url: str, title: str) -> dict:
        with self.connection() as conn:
            conn.execute("INSERT OR IGNORE INTO feeds(url,title,created_at) VALUES(?,?,?)", (url,title,utcnow()))
            row = dict(conn.execute("SELECT * FROM feeds WHERE url=?", (url,)).fetchone())
            self._bump(conn)
            return row

    def list_feed_urls(self) -> list[tuple[int,str]]:
        with self.connection() as conn:
            return [(r["id"], r["url"]) for r in conn.execute("SELECT id,url FROM feeds")]

    def list_feeds(self) -> list[dict]:
        with self.connection() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM feeds ORDER BY id")]

    def get_feed(self, feed_id: int) -> dict:
        with self.connection() as conn:
            self._require(conn,"feeds",feed_id)
            return dict(conn.execute("SELECT * FROM feeds WHERE id=?",(feed_id,)).fetchone())

    def subscribe(self, user_id: int, feed_id: int):
        with self.connection() as conn:
            self._require(conn,"users",user_id)
            self._require(conn,"feeds",feed_id)
            conn.execute("INSERT OR IGNORE INTO subscriptions VALUES(?,?)",(user_id,feed_id))
            self._bump(conn)

    def unsubscribe(self, user_id: int, feed_id: int) -> bool:
        with self.connection() as conn:
            self._require(conn,"users",user_id)
            cur = conn.execute("DELETE FROM subscriptions WHERE user_id=? AND feed_id=?",(user_id,feed_id))
            if cur.rowcount: self._bump(conn)
            return bool(cur.rowcount)

    def user_feeds(self, user_id: int) -> list[dict]:
        with self.connection() as conn:
            self._require(conn,"users",user_id)
            return [dict(r) for r in conn.execute("SELECT f.* FROM feeds f JOIN subscriptions s ON s.feed_id=f.id WHERE s.user_id=? ORDER BY f.id",(user_id,))]

    def create_filter(self, user_id:int, name:str, kind:str, params:dict) -> dict:
        with self.connection() as conn:
            self._require(conn,"users",user_id)
            cur=conn.execute("INSERT INTO filters(user_id,name,kind,params,created_at) VALUES(?,?,?,?,?)",(user_id,name,kind,json.dumps(params,ensure_ascii=False),utcnow()))
            self._bump(conn)
            return self.get_filter_from_conn(conn,cur.lastrowid)

    @staticmethod
    def _filter(row) -> dict:
        d=dict(row);d["params"]=json.loads(d["params"]);return d

    def get_filter_from_conn(self,conn,id_:int) -> dict:
        row=conn.execute("SELECT * FROM filters WHERE id=?",(id_,)).fetchone()
        if row is None: raise NotFound(f"filter {id_} not found")
        return self._filter(row)

    def list_filters(self,user_id:int) -> list[dict]:
        with self.connection() as conn:
            self._require(conn,"users",user_id)
            return [self._filter(r) for r in conn.execute("SELECT * FROM filters WHERE user_id=? ORDER BY id",(user_id,))]

    def update_filter(self,user_id:int,filter_id:int,name:str,kind:str,params:dict) -> dict:
        with self.connection() as conn:
            self._require(conn,"users",user_id)
            cur=conn.execute("UPDATE filters SET name=?,kind=?,params=? WHERE id=? AND user_id=?",(name,kind,json.dumps(params,ensure_ascii=False),filter_id,user_id))
            if not cur.rowcount: raise NotFound("filter not found for user")
            conn.execute("DELETE FROM matches WHERE filter_id=?",(filter_id,))
            self._bump(conn)
            return self.get_filter_from_conn(conn,filter_id)

    def delete_filter(self,user_id:int,filter_id:int) -> bool:
        with self.connection() as conn:
            self._require(conn,"users",user_id)
            cur=conn.execute("DELETE FROM filters WHERE user_id=? AND id=?",(user_id,filter_id))
            if cur.rowcount:self._bump(conn)
            return bool(cur.rowcount)

    def save_articles(self,feed_id:int,articles:Iterable[Article]) -> int:
        count=0
        with self.connection() as conn:
            self._require(conn,"feeds",feed_id)
            for a in articles:
                cur=conn.execute("INSERT OR IGNORE INTO articles(feed_id,guid,title,summary,link,published_at) VALUES(?,?,?,?,?,?)",(feed_id,a.guid,a.title,a.summary,a.link,(a.published_at.isoformat() if a.published_at else utcnow())))
                count+=cur.rowcount
            if count:self._bump(conn)
        return count

    @staticmethod
    def _article(row) -> Article:
        return Article(id=row["id"],feed_id=row["feed_id"],guid=row["guid"],title=row["title"],summary=row["summary"],link=row["link"],published_at=datetime.fromisoformat(row["published_at"]))

    def news_candidates(self,user_id:int,limit:int=200,offset:int=0) -> list[Article]:
        with self.connection() as conn:
            self._require(conn,"users",user_id)
            rows=conn.execute("""SELECT a.* FROM articles a JOIN subscriptions s ON s.feed_id=a.feed_id
                WHERE s.user_id=? ORDER BY a.published_at DESC,a.id DESC LIMIT ? OFFSET ?""",(user_id,limit,offset)).fetchall()
            return [self._article(r) for r in rows]

    def save_matches(self,user_id:int,article_id:int,reasons:list[dict]):
        with self.connection() as conn:
            conn.execute("DELETE FROM matches WHERE user_id=? AND article_id=?",(user_id,article_id))
            for reason in reasons:
                conn.execute("INSERT INTO matches(user_id,article_id,filter_id,reason) VALUES(?,?,?,?)",(user_id,article_id,reason["filter_id"],json.dumps(reason,ensure_ascii=False)))

    def get_vector(self,article_id:int,model:str) -> bytes | None:
        with self.connection() as conn:
            row=conn.execute("SELECT vector FROM article_vectors WHERE article_id=? AND model=?",(article_id,model)).fetchone()
            return row[0] if row else None

    def put_vector(self,article_id:int,model:str,vector:bytes):
        with self.connection() as conn:
            conn.execute("INSERT OR REPLACE INTO article_vectors VALUES(?,?,?)",(article_id,model,vector))
