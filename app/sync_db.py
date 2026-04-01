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


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {
        str(row["name"])
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column in columns:
        return
    connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


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
                        last_sync_run_id TEXT NOT NULL DEFAULT '',
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

                    CREATE TABLE IF NOT EXISTS article_executions (
                        id TEXT PRIMARY KEY,
                        article_id TEXT NOT NULL,
                        article_url TEXT NOT NULL,
                        trigger_channel TEXT NOT NULL DEFAULT 'web',
                        source_type TEXT NOT NULL DEFAULT 'wechat',
                        status TEXT NOT NULL DEFAULT 'queued',
                        ai_enabled INTEGER NOT NULL DEFAULT 0,
                        output_target TEXT NOT NULL DEFAULT 'fns',
                        sync_run_id TEXT NOT NULL DEFAULT '',
                        ingest_job_id TEXT NOT NULL DEFAULT '',
                        rerun_of_execution_id TEXT NOT NULL DEFAULT '',
                        fetch_status TEXT NOT NULL DEFAULT '',
                        content_kind TEXT NOT NULL DEFAULT '',
                        note_title TEXT NOT NULL DEFAULT '',
                        sync_path TEXT NOT NULL DEFAULT '',
                        error_message TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        started_at TEXT NOT NULL DEFAULT '',
                        finished_at TEXT NOT NULL DEFAULT '',
                        FOREIGN KEY(article_id) REFERENCES articles(id)
                    );

                    CREATE INDEX IF NOT EXISTS idx_article_executions_article_created
                        ON article_executions(article_id, created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_article_executions_status
                        ON article_executions(status, source_type, trigger_channel);

                    CREATE TABLE IF NOT EXISTS users (
                        id TEXT PRIMARY KEY,
                        username TEXT NOT NULL UNIQUE,
                        password_hash TEXT NOT NULL,
                        display_name TEXT NOT NULL DEFAULT '',
                        role TEXT NOT NULL DEFAULT 'operator',
                        status TEXT NOT NULL DEFAULT 'active',
                        note TEXT NOT NULL DEFAULT '',
                        last_login_at TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS audit_logs (
                        id TEXT PRIMARY KEY,
                        actor_user_id TEXT NOT NULL DEFAULT '',
                        action TEXT NOT NULL,
                        target_type TEXT NOT NULL DEFAULT '',
                        target_id TEXT NOT NULL DEFAULT '',
                        detail_json TEXT NOT NULL DEFAULT '{}',
                        ip_address TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS scheduler_configs (
                        key TEXT PRIMARY KEY,
                        enabled INTEGER NOT NULL DEFAULT 0,
                        frequency TEXT NOT NULL DEFAULT 'daily',
                        day_of_week INTEGER NOT NULL DEFAULT -1,
                        day_of_month INTEGER NOT NULL DEFAULT -1,
                        time_of_day TEXT NOT NULL DEFAULT '09:00',
                        timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
                        updated_at TEXT NOT NULL,
                        last_run_at TEXT NOT NULL DEFAULT '',
                        last_status TEXT NOT NULL DEFAULT '',
                        last_error TEXT NOT NULL DEFAULT '',
                        paused_until TEXT NOT NULL DEFAULT '',
                        pause_reason TEXT NOT NULL DEFAULT ''
                    );

                    CREATE TABLE IF NOT EXISTS scheduler_runs (
                        id TEXT PRIMARY KEY,
                        scheduler_key TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'running',
                        started_at TEXT NOT NULL,
                        finished_at TEXT NOT NULL DEFAULT '',
                        result_json TEXT NOT NULL DEFAULT '{}',
                        error_message TEXT NOT NULL DEFAULT ''
                    );

                    CREATE TABLE IF NOT EXISTS wechat_mp_credentials (
                        id TEXT PRIMARY KEY,
                        token_encrypted TEXT NOT NULL DEFAULT '',
                        cookie_encrypted TEXT NOT NULL DEFAULT '',
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS wechat_mp_qr_sessions (
                        id TEXT PRIMARY KEY,
                        status TEXT NOT NULL DEFAULT 'pending',
                        uuid_cookie TEXT NOT NULL DEFAULT '',
                        qrcode_url TEXT NOT NULL DEFAULT '',
                        qrcode_bytes_b64 TEXT NOT NULL DEFAULT '',
                        token TEXT NOT NULL DEFAULT '',
                        cookie TEXT NOT NULL DEFAULT '',
                        error_message TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL DEFAULT ''
                    );
                    """
                )
                _ensure_column(connection, "articles", "last_sync_run_id", "TEXT NOT NULL DEFAULT ''")
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
            "last_sync_run_id": str(payload.get("last_sync_run_id") or "").strip(),
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
                    last_sync_run_id = excluded.last_sync_run_id,
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
        source_id: str | None = None,
        sync_run_id: str | None = None,
        has_execution: bool | None = None,
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
        if source_id:
            clauses.append("account_fakeid = (SELECT account_fakeid FROM sync_sources WHERE id = ?)")
            params.append(str(source_id).strip())
        if sync_run_id:
            clauses.append("last_sync_run_id = ?")
            params.append(str(sync_run_id).strip())
        if has_execution is True:
            clauses.append("EXISTS (SELECT 1 FROM article_executions WHERE article_executions.article_id = articles.id)")
        elif has_execution is False:
            clauses.append("NOT EXISTS (SELECT 1 FROM article_executions WHERE article_executions.article_id = articles.id)")
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
        items: list[dict[str, Any]] = []
        for row in rows:
            article = _to_dict(row) or {}
            latest_execution = self.get_latest_article_execution(str(article.get("id") or ""))
            article["latest_execution"] = latest_execution
            article["has_execution"] = latest_execution is not None
            items.append(article)
        return {
            "items": items,
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

    def create_article_execution(
        self,
        *,
        article_id: str,
        article_url: str,
        trigger_channel: str,
        source_type: str,
        status: str = "queued",
        ai_enabled: bool = False,
        output_target: str = "fns",
        sync_run_id: str = "",
        ingest_job_id: str = "",
        rerun_of_execution_id: str = "",
        fetch_status: str = "",
        content_kind: str = "",
        note_title: str = "",
        sync_path: str = "",
        error_message: str = "",
    ) -> dict[str, Any]:
        self.initialize()
        now = _utc_now()
        payload = {
            "id": uuid.uuid4().hex,
            "article_id": str(article_id or "").strip(),
            "article_url": str(article_url or "").strip(),
            "trigger_channel": str(trigger_channel or "web").strip() or "web",
            "source_type": str(source_type or "wechat").strip() or "wechat",
            "status": str(status or "queued").strip() or "queued",
            "ai_enabled": _normalize_bool(ai_enabled),
            "output_target": str(output_target or "fns").strip() or "fns",
            "sync_run_id": str(sync_run_id or "").strip(),
            "ingest_job_id": str(ingest_job_id or "").strip(),
            "rerun_of_execution_id": str(rerun_of_execution_id or "").strip(),
            "fetch_status": str(fetch_status or "").strip(),
            "content_kind": str(content_kind or "").strip(),
            "note_title": str(note_title or "").strip(),
            "sync_path": str(sync_path or "").strip(),
            "error_message": str(error_message or "").strip(),
            "created_at": now,
            "updated_at": now,
            "started_at": now if status == "running" else "",
            "finished_at": now if status in {"success", "error"} else "",
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO article_executions (
                    id, article_id, article_url, trigger_channel, source_type, status, ai_enabled,
                    output_target, sync_run_id, ingest_job_id, rerun_of_execution_id, fetch_status,
                    content_kind, note_title, sync_path, error_message, created_at, updated_at,
                    started_at, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(payload.values()),
            )
            row = connection.execute("SELECT * FROM article_executions WHERE id = ?", (payload["id"],)).fetchone()
        return _to_dict(row) or payload

    def update_article_execution(self, execution_id: str, **fields: Any) -> dict[str, Any] | None:
        self.initialize()
        current = self.get_article_execution(execution_id)
        if current is None:
            return None
        updated = {**current, **fields, "updated_at": _utc_now()}
        status = str(updated.get("status") or "").strip()
        if status == "running" and not str(updated.get("started_at") or "").strip():
            updated["started_at"] = _utc_now()
        if status in {"success", "error"} and not str(updated.get("finished_at") or "").strip():
            updated["finished_at"] = _utc_now()
        columns = [
            "status",
            "ai_enabled",
            "output_target",
            "sync_run_id",
            "ingest_job_id",
            "rerun_of_execution_id",
            "fetch_status",
            "content_kind",
            "note_title",
            "sync_path",
            "error_message",
            "updated_at",
            "started_at",
            "finished_at",
        ]
        with self._connect() as connection:
            connection.execute(
                f"UPDATE article_executions SET {', '.join(f'{column} = ?' for column in columns)} WHERE id = ?",
                tuple(updated[column] for column in columns) + (str(execution_id or "").strip(),),
            )
            row = connection.execute(
                "SELECT * FROM article_executions WHERE id = ?",
                (str(execution_id or "").strip(),),
            ).fetchone()
        return _to_dict(row)

    def get_article_execution(self, execution_id: str) -> dict[str, Any] | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM article_executions WHERE id = ?",
                (str(execution_id or "").strip(),),
            ).fetchone()
        execution = _to_dict(row)
        if execution is not None:
            execution["task_id"] = execution["id"]
        return execution

    def get_latest_article_execution(self, article_id: str) -> dict[str, Any] | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM article_executions
                WHERE article_id = ?
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (str(article_id or "").strip(),),
            ).fetchone()
        execution = _to_dict(row)
        if execution is not None:
            execution["task_id"] = execution["id"]
        return execution

    def list_article_executions(
        self,
        *,
        article_id: str | None = None,
        trigger_channel: str | None = None,
        source_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        self.initialize()
        clauses = ["1=1"]
        params: list[Any] = []
        if article_id:
            clauses.append("article_id = ?")
            params.append(str(article_id).strip())
        if trigger_channel:
            clauses.append("trigger_channel = ?")
            params.append(str(trigger_channel).strip())
        if source_type:
            clauses.append("source_type = ?")
            params.append(str(source_type).strip())
        if status:
            clauses.append("status = ?")
            params.append(str(status).strip())

        where_sql = " AND ".join(clauses)
        with self._connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM article_executions WHERE {where_sql}",
                tuple(params),
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT * FROM article_executions
                WHERE {where_sql}
                ORDER BY created_at DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                tuple(params + [max(int(limit), 1), max(int(offset), 0)]),
            ).fetchall()
        items = []
        for row in rows:
            item = _to_dict(row) or {}
            item["task_id"] = item.get("id")
            items.append(item)
        return {
            "items": items,
            "total": int(total or 0),
            "limit": int(limit),
            "offset": int(offset),
        }

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

    def delete_articles(self, article_ids: list[str]) -> int:
        self.initialize()
        normalized_ids = [str(item).strip() for item in article_ids if str(item).strip()]
        if not normalized_ids:
            return 0
        placeholders = ", ".join("?" for _ in normalized_ids)
        with self._connect() as connection:
            urls = [
                str(row["article_url"] or "")
                for row in connection.execute(
                    f"SELECT article_url FROM articles WHERE id IN ({placeholders})",
                    tuple(normalized_ids),
                ).fetchall()
            ]
            if urls:
                url_placeholders = ", ".join("?" for _ in urls)
                connection.execute(
                    f"DELETE FROM article_artifacts WHERE article_url IN ({url_placeholders})",
                    tuple(urls),
                )
            connection.execute(
                f"DELETE FROM article_executions WHERE article_id IN ({placeholders})",
                tuple(normalized_ids),
            )
            cursor = connection.execute(
                f"DELETE FROM articles WHERE id IN ({placeholders})",
                tuple(normalized_ids),
            )
        return int(cursor.rowcount or 0)

    def find_article_ids(
        self,
        *,
        account_fakeid: str | None = None,
        process_status: str | None = None,
        is_ingested: bool | None = None,
        has_execution: bool | None = None,
        sync_run_id: str | None = None,
        source_id: str | None = None,
        published_from: int | None = None,
        published_to: int | None = None,
    ) -> list[str]:
        self.initialize()
        clauses = ["1=1"]
        params: list[Any] = []
        if account_fakeid:
            clauses.append("account_fakeid = ?")
            params.append(str(account_fakeid).strip())
        if source_id:
            clauses.append("account_fakeid = (SELECT account_fakeid FROM sync_sources WHERE id = ?)")
            params.append(str(source_id).strip())
        if sync_run_id:
            clauses.append("last_sync_run_id = ?")
            params.append(str(sync_run_id).strip())
        if process_status:
            clauses.append("process_status = ?")
            params.append(str(process_status).strip())
        if is_ingested is not None:
            clauses.append("is_ingested = ?")
            params.append(_normalize_bool(is_ingested))
        if has_execution is True:
            clauses.append("EXISTS (SELECT 1 FROM article_executions WHERE article_executions.article_id = articles.id)")
        elif has_execution is False:
            clauses.append("NOT EXISTS (SELECT 1 FROM article_executions WHERE article_executions.article_id = articles.id)")
        if published_from is not None:
            clauses.append("publish_time >= ?")
            params.append(int(published_from))
        if published_to is not None:
            clauses.append("publish_time <= ?")
            params.append(int(published_to))
        where_sql = " AND ".join(clauses)
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT id FROM articles WHERE {where_sql} ORDER BY publish_time DESC, updated_at DESC",
                tuple(params),
            ).fetchall()
        return [str(row["id"]) for row in rows]

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

    def create_or_update_user(
        self,
        *,
        username: str,
        password_hash: str,
        display_name: str,
        role: str,
        status: str = "active",
        note: str = "",
    ) -> dict[str, Any]:
        self.initialize()
        normalized_username = str(username or "").strip()
        if not normalized_username:
            raise ValueError("username 不能为空")
        now = _utc_now()
        payload = {
            "id": uuid.uuid4().hex,
            "username": normalized_username,
            "password_hash": str(password_hash or "").strip(),
            "display_name": str(display_name or "").strip(),
            "role": str(role or "operator").strip() or "operator",
            "status": str(status or "active").strip() or "active",
            "note": str(note or "").strip(),
            "last_login_at": "",
            "created_at": now,
            "updated_at": now,
        }
        with self._connect() as connection:
            existing = connection.execute("SELECT * FROM users WHERE username = ?", (normalized_username,)).fetchone()
            if existing is not None:
                payload["id"] = existing["id"]
                payload["created_at"] = existing["created_at"]
                payload["last_login_at"] = str(existing["last_login_at"] or "")
            connection.execute(
                """
                INSERT INTO users (
                    id, username, password_hash, display_name, role, status, note,
                    last_login_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(username) DO UPDATE SET
                    password_hash = excluded.password_hash,
                    display_name = excluded.display_name,
                    role = excluded.role,
                    status = excluded.status,
                    note = excluded.note,
                    updated_at = excluded.updated_at
                """,
                tuple(payload.values()),
            )
            row = connection.execute("SELECT * FROM users WHERE username = ?", (normalized_username,)).fetchone()
        return _to_dict(row) or payload

    def list_users(self) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM users ORDER BY created_at ASC").fetchall()
        return [_to_dict(row) or {} for row in rows]

    def get_user_by_username(self, username: str) -> dict[str, Any] | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE username = ?", (str(username or "").strip(),)).fetchone()
        return _to_dict(row)

    def get_user_by_id(self, user_id: str) -> dict[str, Any] | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id = ?", (str(user_id or "").strip(),)).fetchone()
        return _to_dict(row)

    def update_user(self, user_id: str, **fields: Any) -> dict[str, Any] | None:
        self.initialize()
        current = self.get_user_by_id(user_id)
        if current is None:
            return None
        allowed = {"display_name", "role", "status", "note", "password_hash", "last_login_at"}
        updates = {key: fields[key] for key in fields if key in allowed}
        if not updates:
            return current
        updates["updated_at"] = _utc_now()
        columns = list(updates.keys())
        with self._connect() as connection:
            connection.execute(
                f"UPDATE users SET {', '.join(f'{column} = ?' for column in columns)} WHERE id = ?",
                tuple(updates[column] for column in columns) + (str(user_id or "").strip(),),
            )
            row = connection.execute("SELECT * FROM users WHERE id = ?", (str(user_id or "").strip(),)).fetchone()
        return _to_dict(row)

    def create_audit_log(
        self,
        *,
        actor_user_id: str,
        action: str,
        target_type: str = "",
        target_id: str = "",
        detail: dict[str, Any] | None = None,
        ip_address: str = "",
    ) -> dict[str, Any]:
        self.initialize()
        payload = {
            "id": uuid.uuid4().hex,
            "actor_user_id": str(actor_user_id or "").strip(),
            "action": str(action or "").strip(),
            "target_type": str(target_type or "").strip(),
            "target_id": str(target_id or "").strip(),
            "detail_json": json.dumps(detail or {}, ensure_ascii=False),
            "ip_address": str(ip_address or "").strip(),
            "created_at": _utc_now(),
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_logs (id, actor_user_id, action, target_type, target_id, detail_json, ip_address, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(payload.values()),
            )
        return payload

    def get_scheduler_configs(self) -> dict[str, dict[str, Any]]:
        self.initialize()
        defaults = {
            "source_sync_schedule": {
                "key": "source_sync_schedule",
                "enabled": False,
                "frequency": "daily",
                "day_of_week": -1,
                "day_of_month": -1,
                "time_of_day": "00:00",
                "timezone": "Asia/Shanghai",
                "last_run_at": "",
                "last_status": "",
                "last_error": "",
                "paused_until": "",
                "pause_reason": "",
            },
            "article_ingest_schedule": {
                "key": "article_ingest_schedule",
                "enabled": False,
                "frequency": "daily",
                "day_of_week": -1,
                "day_of_month": -1,
                "time_of_day": "00:00",
                "timezone": "Asia/Shanghai",
                "last_run_at": "",
                "last_status": "",
                "last_error": "",
                "paused_until": "",
                "pause_reason": "",
            },
        }
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM scheduler_configs").fetchall()
        for row in rows:
            payload = _to_dict(row) or {}
            key = str(payload.get("key") or "")
            if key in defaults:
                payload["enabled"] = bool(payload.get("enabled"))
                freq = str(payload.get("frequency") or "")
                if freq.startswith("interval_"):
                    try:
                        payload["interval_hours"] = int(freq.split("_", 1)[1])
                    except ValueError:
                        payload["interval_hours"] = 24
                else:
                    payload["interval_hours"] = 24
                defaults[key] = payload
        return defaults

    def upsert_scheduler_config(self, key: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.initialize()
        current = self.get_scheduler_configs().get(str(key), {})
        
        freq = str(payload.get("frequency") or current.get("frequency") or "daily")
        if "interval_hours" in payload:
            freq = f"interval_{payload['interval_hours']}"
            
        normalized = {
            **current,
            **payload,
            "key": str(key),
            "enabled": _normalize_bool(payload.get("enabled", current.get("enabled", False))),
            "frequency": freq,
            "day_of_week": int(payload.get("day_of_week", current.get("day_of_week", -1)) or -1),
            "day_of_month": int(payload.get("day_of_month", current.get("day_of_month", -1)) or -1),
            "time_of_day": str(payload.get("time_of_day") or current.get("time_of_day") or "09:00"),
            "timezone": str(payload.get("timezone") or current.get("timezone") or "Asia/Shanghai"),
            "updated_at": _utc_now(),
            "last_run_at": str(payload.get("last_run_at") or current.get("last_run_at") or ""),
            "last_status": str(payload.get("last_status") or current.get("last_status") or ""),
            "last_error": str(payload.get("last_error") or current.get("last_error") or ""),
            "paused_until": str(payload.get("paused_until") or current.get("paused_until") or ""),
            "pause_reason": str(payload.get("pause_reason") or current.get("pause_reason") or ""),
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO scheduler_configs (
                    key, enabled, frequency, day_of_week, day_of_month, time_of_day, timezone,
                    updated_at, last_run_at, last_status, last_error, paused_until, pause_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    enabled = excluded.enabled,
                    frequency = excluded.frequency,
                    day_of_week = excluded.day_of_week,
                    day_of_month = excluded.day_of_month,
                    time_of_day = excluded.time_of_day,
                    timezone = excluded.timezone,
                    updated_at = excluded.updated_at,
                    last_run_at = excluded.last_run_at,
                    last_status = excluded.last_status,
                    last_error = excluded.last_error,
                    paused_until = excluded.paused_until,
                    pause_reason = excluded.pause_reason
                """,
                (
                    normalized["key"],
                    normalized["enabled"],
                    normalized["frequency"],
                    normalized["day_of_week"],
                    normalized["day_of_month"],
                    normalized["time_of_day"],
                    normalized["timezone"],
                    normalized["updated_at"],
                    normalized["last_run_at"],
                    normalized["last_status"],
                    normalized["last_error"],
                    normalized["paused_until"],
                    normalized["pause_reason"],
                ),
            )
        return self.get_scheduler_configs()[str(key)]

    def create_scheduler_run(self, scheduler_key: str) -> dict[str, Any]:
        self.initialize()
        payload = {
            "id": uuid.uuid4().hex,
            "scheduler_key": str(scheduler_key or "").strip(),
            "status": "running",
            "started_at": _utc_now(),
            "finished_at": "",
            "result_json": "{}",
            "error_message": "",
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO scheduler_runs (id, scheduler_key, status, started_at, finished_at, result_json, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(payload.values()),
            )
        return payload

    def finish_scheduler_run(self, run_id: str, *, status: str, result: dict[str, Any] | None = None, error_message: str = "") -> None:
        self.initialize()
        now = _utc_now()
        with self._connect() as connection:
            row = connection.execute("SELECT scheduler_key FROM scheduler_runs WHERE id = ?", (str(run_id or "").strip(),)).fetchone()
            connection.execute(
                """
                UPDATE scheduler_runs
                SET status = ?, finished_at = ?, result_json = ?, error_message = ?
                WHERE id = ?
                """,
                (str(status or "completed"), now, json.dumps(result or {}, ensure_ascii=False), str(error_message or ""), str(run_id or "").strip()),
            )
            if row is not None:
                connection.execute(
                    """
                    UPDATE scheduler_configs
                    SET last_run_at = ?, last_status = ?, last_error = ?
                    WHERE key = ?
                    """,
                    (now, str(status or "completed"), str(error_message or ""), str(row["scheduler_key"] or "")),
                )

    def list_scheduler_runs(self, scheduler_key: str, limit: int = 20) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM scheduler_runs WHERE scheduler_key = ?
                ORDER BY started_at DESC LIMIT ?
                """,
                (str(scheduler_key or "").strip(), max(int(limit), 1)),
            ).fetchall()
        return [_to_dict(row) or {} for row in rows]

    def save_wechat_mp_credentials(self, *, token_encrypted: str, cookie_encrypted: str) -> dict[str, Any]:
        self.initialize()
        payload = {
            "id": "default",
            "token_encrypted": str(token_encrypted or ""),
            "cookie_encrypted": str(cookie_encrypted or ""),
            "updated_at": _utc_now(),
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO wechat_mp_credentials (id, token_encrypted, cookie_encrypted, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    token_encrypted = excluded.token_encrypted,
                    cookie_encrypted = excluded.cookie_encrypted,
                    updated_at = excluded.updated_at
                """,
                tuple(payload.values()),
            )
        return payload

    def get_wechat_mp_credentials(self) -> dict[str, Any] | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM wechat_mp_credentials WHERE id = 'default'").fetchone()
        return _to_dict(row)

    def create_wechat_mp_qr_session(
        self,
        *,
        qrcode_url: str,
        uuid_cookie: str,
        qrcode_bytes_b64: str = "",
        expires_at: str = "",
    ) -> dict[str, Any]:
        self.initialize()
        now = _utc_now()
        payload = {
            "id": uuid.uuid4().hex,
            "status": "pending",
            "uuid_cookie": str(uuid_cookie or ""),
            "qrcode_url": str(qrcode_url or ""),
            "qrcode_bytes_b64": str(qrcode_bytes_b64 or ""),
            "token": "",
            "cookie": "",
            "error_message": "",
            "created_at": now,
            "updated_at": now,
            "expires_at": str(expires_at or ""),
        }
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO wechat_mp_qr_sessions (
                    id, status, uuid_cookie, qrcode_url, qrcode_bytes_b64, token, cookie,
                    error_message, created_at, updated_at, expires_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tuple(payload.values()),
            )
        return payload

    def update_wechat_mp_qr_session(self, session_id: str, **fields: Any) -> dict[str, Any] | None:
        self.initialize()
        current = self.get_wechat_mp_qr_session(session_id)
        if current is None:
            return None
        updated = {**current, **fields, "updated_at": _utc_now()}
        columns = [
            "status",
            "uuid_cookie",
            "qrcode_url",
            "qrcode_bytes_b64",
            "token",
            "cookie",
            "error_message",
            "updated_at",
            "expires_at",
        ]
        with self._connect() as connection:
            connection.execute(
                f"UPDATE wechat_mp_qr_sessions SET {', '.join(f'{column} = ?' for column in columns)} WHERE id = ?",
                tuple(updated[column] for column in columns) + (str(session_id or "").strip(),),
            )
            row = connection.execute("SELECT * FROM wechat_mp_qr_sessions WHERE id = ?", (str(session_id or "").strip(),)).fetchone()
        return _to_dict(row)

    def get_wechat_mp_qr_session(self, session_id: str) -> dict[str, Any] | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM wechat_mp_qr_sessions WHERE id = ?", (str(session_id or "").strip(),)).fetchone()
        return _to_dict(row)

    def encode_json(self, payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, indent=2)
