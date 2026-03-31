from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def _normalize_bool(value: Any) -> int:
    return 1 if bool(value) else 0


class SyncStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialized = False

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._lock:
            if self._initialized:
                return
            with self._connect() as connection:
                connection.executescript(
                    """
                    PRAGMA journal_mode=WAL;

                    CREATE TABLE IF NOT EXISTS accounts (
                        id TEXT PRIMARY KEY,
                        fakeid TEXT NOT NULL UNIQUE,
                        biz TEXT NOT NULL DEFAULT '',
                        nickname TEXT NOT NULL DEFAULT '',
                        alias TEXT NOT NULL DEFAULT '',
                        round_head_img TEXT NOT NULL DEFAULT '',
                        service_type INTEGER NOT NULL DEFAULT 0,
                        signature TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'active',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        last_sync_at TEXT NOT NULL DEFAULT ''
                    );

                    CREATE TABLE IF NOT EXISTS sync_sources (
                        id TEXT PRIMARY KEY,
                        account_fakeid TEXT NOT NULL UNIQUE,
                        enabled INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        last_sync_at TEXT NOT NULL DEFAULT '',
                        last_range_start TEXT NOT NULL DEFAULT '',
                        last_range_end TEXT NOT NULL DEFAULT '',
                        latest_article_update_time INTEGER NOT NULL DEFAULT 0,
                        FOREIGN KEY(account_fakeid) REFERENCES accounts(fakeid)
                    );

                    CREATE TABLE IF NOT EXISTS articles (
                        id TEXT PRIMARY KEY,
                        article_url TEXT NOT NULL UNIQUE,
                        source_type TEXT NOT NULL DEFAULT 'wechat',
                        account_fakeid TEXT NOT NULL DEFAULT '',
                        account_name TEXT NOT NULL DEFAULT '',
                        title TEXT NOT NULL DEFAULT '',
                        author TEXT NOT NULL DEFAULT '',
                        digest TEXT NOT NULL DEFAULT '',
                        cover TEXT NOT NULL DEFAULT '',
                        publish_time INTEGER NOT NULL DEFAULT 0,
                        create_time INTEGER NOT NULL DEFAULT 0,
                        content_kind TEXT NOT NULL DEFAULT 'unknown',
                        fetch_status TEXT NOT NULL DEFAULT 'indexed',
                        process_status TEXT NOT NULL DEFAULT 'pending',
                        is_ingested INTEGER NOT NULL DEFAULT 0,
                        cleaned_at TEXT NOT NULL DEFAULT '',
                        ingested_at TEXT NOT NULL DEFAULT '',
                        last_task_id TEXT NOT NULL DEFAULT '',
                        last_error TEXT NOT NULL DEFAULT '',
                        comment_id TEXT NOT NULL DEFAULT '',
                        cache_key TEXT NOT NULL DEFAULT '',
                        cache_hit_count INTEGER NOT NULL DEFAULT 0,
                        raw_html_path TEXT NOT NULL DEFAULT '',
                        normalized_json_path TEXT NOT NULL DEFAULT '',
                        latest_markdown_path TEXT NOT NULL DEFAULT '',
                        content_hash TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_articles_account_publish
                        ON articles(account_fakeid, publish_time DESC);
                    CREATE INDEX IF NOT EXISTS idx_articles_process_status
                        ON articles(process_status, is_ingested);

                    CREATE TABLE IF NOT EXISTS sync_runs (
                        id TEXT PRIMARY KEY,
                        sync_source_id TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'running',
                        mode TEXT NOT NULL DEFAULT 'manual',
                        range_start TEXT NOT NULL DEFAULT '',
                        range_end TEXT NOT NULL DEFAULT '',
                        fetched_count INTEGER NOT NULL DEFAULT 0,
                        new_count INTEGER NOT NULL DEFAULT 0,
                        updated_count INTEGER NOT NULL DEFAULT 0,
                        queued_count INTEGER NOT NULL DEFAULT 0,
                        error_message TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        finished_at TEXT NOT NULL DEFAULT '',
                        FOREIGN KEY(sync_source_id) REFERENCES sync_sources(id)
                    );

                    CREATE TABLE IF NOT EXISTS article_artifacts (
                        id TEXT PRIMARY KEY,
                        article_url TEXT NOT NULL,
                        artifact_type TEXT NOT NULL,
                        path TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'ready',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(article_url, artifact_type, path)
                    );

                    CREATE TABLE IF NOT EXISTS ingest_jobs (
                        id TEXT PRIMARY KEY,
                        status TEXT NOT NULL DEFAULT 'queued',
                        total INTEGER NOT NULL DEFAULT 0,
                        completed INTEGER NOT NULL DEFAULT 0,
                        success_count INTEGER NOT NULL DEFAULT 0,
                        failure_count INTEGER NOT NULL DEFAULT 0,
                        ai_enabled INTEGER NOT NULL DEFAULT 0,
                        output_target TEXT NOT NULL DEFAULT 'fns',
                        skip_ingested INTEGER NOT NULL DEFAULT 1,
                        error_message TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        finished_at TEXT NOT NULL DEFAULT ''
                    );
                    """
                )
            self._initialized = True

    def build_cache_key(self, article_url: str) -> str:
        return hashlib.sha256(str(article_url).strip().encode("utf-8")).hexdigest()

    def upsert_account(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.initialize()
        fakeid = str(payload.get("fakeid") or "").strip()
        if not fakeid:
            raise ValueError("fakeid 不能为空")
        now = _utc_now()
        account = {
            "id": str(payload.get("id") or uuid.uuid4().hex),
            "fakeid": fakeid,
            "biz": str(payload.get("biz") or "").strip(),
            "nickname": str(payload.get("nickname") or "").strip(),
            "alias": str(payload.get("alias") or "").strip(),
            "round_head_img": str(payload.get("round_head_img") or "").strip(),
            "service_type": int(payload.get("service_type") or 0),
            "signature": str(payload.get("signature") or "").strip(),
            "status": str(payload.get("status") or "active").strip() or "active",
            "created_at": now,
            "updated_at": now,
            "last_sync_at": str(payload.get("last_sync_at") or "").strip(),
        }
        with self._connect() as connection:
            existing = connection.execute("SELECT id, created_at, last_sync_at FROM accounts WHERE fakeid = ?", (fakeid,)).fetchone()
            if existing is not None:
                account["id"] = existing["id"]
                account["created_at"] = existing["created_at"]
                if not account["last_sync_at"]:
                    account["last_sync_at"] = existing["last_sync_at"]
            connection.execute(
                """
                INSERT INTO accounts (
                    id, fakeid, biz, nickname, alias, round_head_img, service_type,
                    signature, status, created_at, updated_at, last_sync_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fakeid) DO UPDATE SET
                    biz = excluded.biz,
                    nickname = excluded.nickname,
                    alias = excluded.alias,
                    round_head_img = excluded.round_head_img,
                    service_type = excluded.service_type,
                    signature = excluded.signature,
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    last_sync_at = excluded.last_sync_at
                """,
                (
                    account["id"],
                    account["fakeid"],
                    account["biz"],
                    account["nickname"],
                    account["alias"],
                    account["round_head_img"],
                    account["service_type"],
                    account["signature"],
                    account["status"],
                    account["created_at"],
                    account["updated_at"],
                    account["last_sync_at"],
                ),
            )
            row = connection.execute("SELECT * FROM accounts WHERE fakeid = ?", (fakeid,)).fetchone()
        return _to_dict(row) or account

    def create_or_update_sync_source(self, account_fakeid: str) -> dict[str, Any]:
        self.initialize()
        normalized = str(account_fakeid or "").strip()
        if not normalized:
            raise ValueError("account_fakeid 不能为空")
        now = _utc_now()
        source = {
            "id": uuid.uuid4().hex,
            "account_fakeid": normalized,
            "enabled": 1,
            "created_at": now,
            "updated_at": now,
            "last_sync_at": "",
            "last_range_start": "",
            "last_range_end": "",
            "latest_article_update_time": 0,
        }
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM sync_sources WHERE account_fakeid = ?",
                (normalized,),
            ).fetchone()
            if existing is not None:
                source["id"] = existing["id"]
                source["created_at"] = existing["created_at"]
                source["last_sync_at"] = existing["last_sync_at"]
                source["last_range_start"] = existing["last_range_start"]
                source["last_range_end"] = existing["last_range_end"]
                source["latest_article_update_time"] = int(existing["latest_article_update_time"] or 0)
            connection.execute(
                """
                INSERT INTO sync_sources (
                    id, account_fakeid, enabled, created_at, updated_at, last_sync_at,
                    last_range_start, last_range_end, latest_article_update_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(account_fakeid) DO UPDATE SET
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    source["id"],
                    source["account_fakeid"],
                    source["enabled"],
                    source["created_at"],
                    source["updated_at"],
                    source["last_sync_at"],
                    source["last_range_start"],
                    source["last_range_end"],
                    source["latest_article_update_time"],
                ),
            )
            row = connection.execute(
                """
                SELECT sync_sources.*, accounts.nickname AS account_name, accounts.alias AS account_alias
                FROM sync_sources
                LEFT JOIN accounts ON accounts.fakeid = sync_sources.account_fakeid
                WHERE sync_sources.account_fakeid = ?
                """,
                (normalized,),
            ).fetchone()
        return _to_dict(row) or source

    def list_sync_sources(self) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT sync_sources.*, accounts.nickname AS account_name, accounts.alias AS account_alias
                FROM sync_sources
                LEFT JOIN accounts ON accounts.fakeid = sync_sources.account_fakeid
                ORDER BY sync_sources.created_at DESC
                """
            ).fetchall()
        return [_to_dict(row) or {} for row in rows]

    def delete_sync_source(self, source_id: str) -> None:
        self.initialize()
        with self._connect() as connection:
            connection.execute("DELETE FROM sync_sources WHERE id = ?", (str(source_id or "").strip(),))

    def get_sync_source(self, source_id: str) -> dict[str, Any] | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT sync_sources.*, accounts.nickname AS account_name, accounts.alias AS account_alias
                FROM sync_sources
                LEFT JOIN accounts ON accounts.fakeid = sync_sources.account_fakeid
                WHERE sync_sources.id = ?
                """,
                (str(source_id or "").strip(),),
            ).fetchone()
        return _to_dict(row)

    def update_sync_source_state(
        self,
        source_id: str,
        *,
        last_sync_at: str,
        last_range_start: str,
        last_range_end: str,
        latest_article_update_time: int,
    ) -> None:
        self.initialize()
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE sync_sources
                SET updated_at = ?, last_sync_at = ?, last_range_start = ?, last_range_end = ?,
                    latest_article_update_time = ?
                WHERE id = ?
                """,
                (
                    now,
                    str(last_sync_at or "").strip(),
                    str(last_range_start or "").strip(),
                    str(last_range_end or "").strip(),
                    int(latest_article_update_time or 0),
                    str(source_id or "").strip(),
                ),
            )
            connection.execute(
                """
                UPDATE accounts
                SET updated_at = ?, last_sync_at = ?
                WHERE fakeid = (SELECT account_fakeid FROM sync_sources WHERE id = ?)
                """,
                (now, str(last_sync_at or "").strip(), str(source_id or "").strip()),
            )

    def create_sync_run(self, sync_source_id: str, *, mode: str, range_start: str, range_end: str) -> dict[str, Any]:
        self.initialize()
        now = _utc_now()
        payload = {
            "id": uuid.uuid4().hex,
            "sync_source_id": str(sync_source_id or "").strip(),
            "status": "running",
            "mode": str(mode or "manual").strip() or "manual",
            "range_start": str(range_start or "").strip(),
            "range_end": str(range_end or "").strip(),
            "fetched_count": 0,
            "new_count": 0,
            "updated_count": 0,
            "queued_count": 0,
            "error_message": "",
            "created_at": now,
            "updated_at": now,
            "finished_at": "",
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO sync_runs (
                    id, sync_source_id, status, mode, range_start, range_end, fetched_count,
                    new_count, updated_count, queued_count, error_message, created_at, updated_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(payload.values()),
            )
        return payload

    def finish_sync_run(self, run_id: str, *, status: str, fetched_count: int, new_count: int, updated_count: int, queued_count: int, error_message: str = "") -> None:
        self.initialize()
        now = _utc_now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE sync_runs
                SET status = ?, fetched_count = ?, new_count = ?, updated_count = ?, queued_count = ?,
                    error_message = ?, updated_at = ?, finished_at = ?
                WHERE id = ?
                """,
                (
                    str(status or "completed").strip(),
                    int(fetched_count or 0),
                    int(new_count or 0),
                    int(updated_count or 0),
                    int(queued_count or 0),
                    str(error_message or ""),
                    now,
                    now,
                    str(run_id or "").strip(),
                ),
            )

    def upsert_article(self, payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        self.initialize()
        article_url = str(payload.get("article_url") or payload.get("url") or "").strip()
        if not article_url:
            raise ValueError("article_url 不能为空")
        now = _utc_now()
        article = {
            "id": str(payload.get("id") or uuid.uuid4().hex),
            "article_url": article_url,
            "source_type": str(payload.get("source_type") or "wechat").strip() or "wechat",
            "account_fakeid": str(payload.get("account_fakeid") or "").strip(),
            "account_name": str(payload.get("account_name") or "").strip(),
            "title": str(payload.get("title") or "").strip(),
            "author": str(payload.get("author") or "").strip(),
            "digest": str(payload.get("digest") or "").strip(),
            "cover": str(payload.get("cover") or "").strip(),
            "publish_time": int(payload.get("publish_time") or 0),
            "create_time": int(payload.get("create_time") or 0),
            "content_kind": str(payload.get("content_kind") or "unknown").strip() or "unknown",
            "fetch_status": str(payload.get("fetch_status") or "indexed").strip() or "indexed",
            "process_status": str(payload.get("process_status") or "pending").strip() or "pending",
            "is_ingested": _normalize_bool(payload.get("is_ingested")),
            "cleaned_at": str(payload.get("cleaned_at") or "").strip(),
            "ingested_at": str(payload.get("ingested_at") or "").strip(),
            "last_task_id": str(payload.get("last_task_id") or "").strip(),
            "last_error": str(payload.get("last_error") or "").strip(),
            "comment_id": str(payload.get("comment_id") or "").strip(),
            "cache_key": str(payload.get("cache_key") or self.build_cache_key(article_url)).strip(),
            "cache_hit_count": int(payload.get("cache_hit_count") or 0),
            "raw_html_path": str(payload.get("raw_html_path") or "").strip(),
            "normalized_json_path": str(payload.get("normalized_json_path") or "").strip(),
            "latest_markdown_path": str(payload.get("latest_markdown_path") or "").strip(),
            "content_hash": str(payload.get("content_hash") or "").strip(),
            "created_at": now,
            "updated_at": now,
        }
        columns = list(article.keys())
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT id, created_at, cache_hit_count, is_ingested, cleaned_at, ingested_at FROM articles WHERE article_url = ?",
                (article_url,),
            ).fetchone()
            is_new = existing is None
            if existing is not None:
                article["id"] = existing["id"]
                article["created_at"] = existing["created_at"]
                if not article["cleaned_at"]:
                    article["cleaned_at"] = str(existing["cleaned_at"] or "")
                if not article["ingested_at"]:
                    article["ingested_at"] = str(existing["ingested_at"] or "")
                if not article["is_ingested"]:
                    article["is_ingested"] = int(existing["is_ingested"] or 0)
                article["cache_hit_count"] = max(int(existing["cache_hit_count"] or 0), int(article["cache_hit_count"]))
            connection.execute(
                f"""
                INSERT INTO articles ({", ".join(columns)})
                VALUES ({", ".join("?" for _ in columns)})
                ON CONFLICT(article_url) DO UPDATE SET
                    source_type = excluded.source_type,
                    account_fakeid = excluded.account_fakeid,
                    account_name = excluded.account_name,
                    title = excluded.title,
                    author = excluded.author,
                    digest = excluded.digest,
                    cover = excluded.cover,
                    publish_time = excluded.publish_time,
                    create_time = excluded.create_time,
                    content_kind = excluded.content_kind,
                    fetch_status = excluded.fetch_status,
                    process_status = excluded.process_status,
                    is_ingested = excluded.is_ingested,
                    cleaned_at = excluded.cleaned_at,
                    ingested_at = excluded.ingested_at,
                    last_task_id = excluded.last_task_id,
                    last_error = excluded.last_error,
                    comment_id = excluded.comment_id,
                    cache_key = excluded.cache_key,
                    cache_hit_count = excluded.cache_hit_count,
                    raw_html_path = excluded.raw_html_path,
                    normalized_json_path = excluded.normalized_json_path,
                    latest_markdown_path = excluded.latest_markdown_path,
                    content_hash = excluded.content_hash,
                    updated_at = excluded.updated_at
                """,
                tuple(article[column] for column in columns),
            )
            row = connection.execute("SELECT * FROM articles WHERE article_url = ?", (article_url,)).fetchone()
        return (_to_dict(row) or article, is_new)

    def get_article_by_id(self, article_id: str) -> dict[str, Any] | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM articles WHERE id = ?", (str(article_id or "").strip(),)).fetchone()
        return _to_dict(row)

    def get_article_by_url(self, article_url: str) -> dict[str, Any] | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM articles WHERE article_url = ?", (str(article_url or "").strip(),)).fetchone()
        return _to_dict(row)

    def list_articles(
        self,
        *,
        account_fakeid: str | None = None,
        process_status: str | None = None,
        is_ingested: bool | None = None,
        published_from: int | None = None,
        published_to: int | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        self.initialize()
        clauses = ["1=1"]
        params: list[Any] = []
        if account_fakeid:
            clauses.append("account_fakeid = ?")
            params.append(str(account_fakeid).strip())
        if process_status:
            clauses.append("process_status = ?")
            params.append(str(process_status).strip())
        if is_ingested is not None:
            clauses.append("is_ingested = ?")
            params.append(_normalize_bool(is_ingested))
        if published_from is not None:
            clauses.append("publish_time >= ?")
            params.append(int(published_from))
        if published_to is not None:
            clauses.append("publish_time <= ?")
            params.append(int(published_to))

        where_sql = " AND ".join(clauses)
        with self._connect() as connection:
            total = connection.execute(f"SELECT COUNT(*) FROM articles WHERE {where_sql}", tuple(params)).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT * FROM articles
                WHERE {where_sql}
                ORDER BY publish_time DESC, updated_at DESC
                LIMIT ? OFFSET ?
                """,
                tuple(params + [max(int(limit), 1), max(int(offset), 0)]),
            ).fetchall()
        return {
            "items": [_to_dict(row) or {} for row in rows],
            "total": int(total or 0),
            "limit": int(limit),
            "offset": int(offset),
        }

    def update_article_status(
        self,
        article_url: str,
        *,
        fetch_status: str | None = None,
        process_status: str | None = None,
        is_ingested: bool | None = None,
        cleaned_at: str | None = None,
        ingested_at: str | None = None,
        last_task_id: str | None = None,
        last_error: str | None = None,
        cache_hit: bool | None = None,
        latest_markdown_path: str | None = None,
    ) -> None:
        self.initialize()
        updates: list[str] = ["updated_at = ?"]
        params: list[Any] = [_utc_now()]
        if fetch_status is not None:
            updates.append("fetch_status = ?")
            params.append(str(fetch_status).strip())
        if process_status is not None:
            updates.append("process_status = ?")
            params.append(str(process_status).strip())
        if is_ingested is not None:
            updates.append("is_ingested = ?")
            params.append(_normalize_bool(is_ingested))
        if cleaned_at is not None:
            updates.append("cleaned_at = ?")
            params.append(str(cleaned_at).strip())
        if ingested_at is not None:
            updates.append("ingested_at = ?")
            params.append(str(ingested_at).strip())
        if last_task_id is not None:
            updates.append("last_task_id = ?")
            params.append(str(last_task_id).strip())
        if last_error is not None:
            updates.append("last_error = ?")
            params.append(str(last_error))
        if latest_markdown_path is not None:
            updates.append("latest_markdown_path = ?")
            params.append(str(latest_markdown_path).strip())
        if cache_hit is True:
            updates.append("cache_hit_count = cache_hit_count + 1")
        params.append(str(article_url or "").strip())
        with self._connect() as connection:
            connection.execute(f"UPDATE articles SET {', '.join(updates)} WHERE article_url = ?", tuple(params))

    def record_artifact(self, article_url: str, artifact_type: str, path: str, *, status: str = "ready") -> dict[str, Any]:
        self.initialize()
        now = _utc_now()
        payload = {
            "id": uuid.uuid4().hex,
            "article_url": str(article_url or "").strip(),
            "artifact_type": str(artifact_type or "").strip(),
            "path": str(path or "").strip(),
            "status": str(status or "ready").strip() or "ready",
            "created_at": now,
            "updated_at": now,
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO article_artifacts (id, article_url, artifact_type, path, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(article_url, artifact_type, path) DO UPDATE SET
                    status = excluded.status,
                    updated_at = excluded.updated_at
                """,
                tuple(payload.values()),
            )
        return payload

    def list_artifacts(self, article_url: str) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM article_artifacts WHERE article_url = ? ORDER BY created_at DESC",
                (str(article_url or "").strip(),),
            ).fetchall()
        return [_to_dict(row) or {} for row in rows]

    def create_ingest_job(self, *, total: int, ai_enabled: bool, output_target: str, skip_ingested: bool) -> dict[str, Any]:
        self.initialize()
        now = _utc_now()
        payload = {
            "id": uuid.uuid4().hex,
            "status": "queued",
            "total": int(total or 0),
            "completed": 0,
            "success_count": 0,
            "failure_count": 0,
            "ai_enabled": _normalize_bool(ai_enabled),
            "output_target": str(output_target or "fns").strip() or "fns",
            "skip_ingested": _normalize_bool(skip_ingested),
            "error_message": "",
            "created_at": now,
            "updated_at": now,
            "finished_at": "",
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO ingest_jobs (
                    id, status, total, completed, success_count, failure_count, ai_enabled,
                    output_target, skip_ingested, error_message, created_at, updated_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(payload.values()),
            )
        return payload

    def update_ingest_job(self, job_id: str, **fields: Any) -> dict[str, Any] | None:
        self.initialize()
        current = self.get_ingest_job(job_id)
        if current is None:
            return None
        updated = {**current, **fields, "updated_at": _utc_now()}
        if updated.get("status") in {"completed", "error"} and not updated.get("finished_at"):
            updated["finished_at"] = _utc_now()
        columns = [
            "status",
            "total",
            "completed",
            "success_count",
            "failure_count",
            "ai_enabled",
            "output_target",
            "skip_ingested",
            "error_message",
            "updated_at",
            "finished_at",
        ]
        with self._connect() as connection:
            connection.execute(
                f"UPDATE ingest_jobs SET {', '.join(f'{column} = ?' for column in columns)} WHERE id = ?",
                tuple(updated[column] for column in columns) + (str(job_id or "").strip(),),
            )
            row = connection.execute("SELECT * FROM ingest_jobs WHERE id = ?", (str(job_id or "").strip(),)).fetchone()
        return _to_dict(row)

    def get_ingest_job(self, job_id: str) -> dict[str, Any] | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM ingest_jobs WHERE id = ?", (str(job_id or "").strip(),)).fetchone()
        return _to_dict(row)

    def encode_json(self, payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2)
