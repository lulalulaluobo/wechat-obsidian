from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.core.pipeline import ArticleData


def get_source_cache_root() -> Path:
    settings = get_settings()
    root = (settings.runtime_config_path.parent / "source-cache").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def build_source_cache_key(url: str) -> str:
    return hashlib.sha256(str(url).strip().encode("utf-8")).hexdigest()


def build_source_cache_paths(url: str) -> dict[str, Path]:
    cache_key = build_source_cache_key(url)
    root = get_source_cache_root() / cache_key[:2] / cache_key
    root.mkdir(parents=True, exist_ok=True)
    return {
        "root": root,
        "html": root / "source.html",
        "normalized": root / "normalized.json",
    }


def load_cached_source(url: str) -> dict[str, Any] | None:
    paths = build_source_cache_paths(url)
    if not paths["html"].exists() or not paths["normalized"].exists():
        return None
    try:
        payload = json.loads(paths["normalized"].read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    article_payload = payload.get("article") if isinstance(payload.get("article"), dict) else {}
    diagnostics = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    source_html = paths["html"].read_text(encoding="utf-8")
    return {
        "cache_key": build_source_cache_key(url),
        "source_html": source_html,
        "source_html_path": str(paths["html"]),
        "normalized_path": str(paths["normalized"]),
        "article": ArticleData(
            title=str(article_payload.get("title") or "未命名文章"),
            author=str(article_payload.get("author") or ""),
            account_name=str(article_payload.get("account_name") or ""),
            content_html=str(article_payload.get("content_html") or ""),
            original_url=str(article_payload.get("original_url") or url),
        ),
        "diagnostics": diagnostics,
    }


def write_source_cache(url: str, *, article: ArticleData, source_html: str, diagnostics: dict[str, Any]) -> dict[str, str]:
    paths = build_source_cache_paths(url)
    paths["html"].write_text(source_html, encoding="utf-8")
    paths["normalized"].write_text(
        json.dumps(
            {
                "article": {
                    "title": article.title,
                    "author": article.author,
                    "account_name": article.account_name,
                    "content_html": article.content_html,
                    "original_url": article.original_url,
                },
                "diagnostics": diagnostics,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {
        "cache_key": build_source_cache_key(url),
        "source_html_path": str(paths["html"]),
        "normalized_path": str(paths["normalized"]),
    }
