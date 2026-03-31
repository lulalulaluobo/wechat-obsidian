from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from readability import Document

from app.core.pipeline import ArticleData, WeChatArticlePipeline
from app.source_cache import load_cached_source, write_source_cache


URL_PATTERN = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Upgrade-Insecure-Requests": "1",
}


def detect_source_type(url: str) -> str:
    host = _extract_host(url)
    if "mp.weixin.qq.com" in host or "weixin.qq.com" in host:
        return "wechat"
    if host.endswith("zhihu.com") or "zhihu.com" in host:
        raise ValueError("知乎链接暂不支持，请改用公众号或普通网页链接")
    if host:
        return "web"
    raise ValueError("无法识别输入链接")


def extract_candidate_urls(text: str) -> list[str]:
    candidates = [item.rstrip(".,);]}>\"'") for item in URL_PATTERN.findall(text or "")]
    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if item not in seen:
            deduped.append(item)
            seen.add(item)
    return deduped


def fetch_article_from_url(
    url: str,
    *,
    timeout: int,
    http_session=None,
) -> tuple[str, ArticleData, str, dict[str, Any]]:
    source_type = detect_source_type(url)
    try:
        cached = load_cached_source(url)
    except RuntimeError:
        cached = None
    if cached is not None:
        diagnostics = dict(cached.get("diagnostics") or {})
        diagnostics["cache_hit"] = True
        diagnostics["cache_key"] = str(cached.get("cache_key") or "")
        diagnostics["source_html_path"] = str(cached.get("source_html_path") or "")
        diagnostics["normalized_path"] = str(cached.get("normalized_path") or "")
        return source_type, cached["article"], str(cached["source_html"]), diagnostics
    if source_type == "wechat":
        return _fetch_wechat_article(url, timeout=timeout, http_session=http_session)
    return _fetch_readable_article(url, source_type=source_type, timeout=timeout, http_session=http_session)


def _fetch_wechat_article(
    url: str,
    *,
    timeout: int,
    http_session=None,
) -> tuple[str, ArticleData, str, dict[str, Any]]:
    pipeline = WeChatArticlePipeline(timeout=timeout)
    if http_session is not None:
        pipeline.session = http_session
    if not pipeline.validate_url(url):
        raise ValueError("无效的微信文章链接，仅支持 mp.weixin.qq.com 或 weixin.qq.com")
    source_html = pipeline.fetch_html(url)
    diagnostics = inspect_wechat_source_html(source_html)
    if diagnostics["fetch_status"] != "success":
        raise RuntimeError(str(diagnostics.get("failure_reason") or "微信公众号文章抓取失败"))
    article = pipeline.extract_article(source_html, url)
    try:
        cache_paths = write_source_cache(url, article=article, source_html=source_html, diagnostics=diagnostics)
        diagnostics.update(cache_paths)
    except RuntimeError:
        pass
    diagnostics["cache_hit"] = False
    return "wechat", article, source_html, diagnostics


def _fetch_readable_article(
    url: str,
    *,
    source_type: str,
    timeout: int,
    http_session=None,
) -> tuple[str, ArticleData, str, dict[str, Any]]:
    session = http_session or requests.Session()
    response = session.get(
        url,
        timeout=timeout,
        allow_redirects=True,
        headers=BROWSER_HEADERS,
    )
    response.raise_for_status()
    source_html = _read_response_text(response)
    article = _build_readable_article(source_html=source_html, original_url=url)
    if _looks_like_unusable_article(article):
        raise RuntimeError("网页正文提取失败，目标站点可能启用了反爬或动态渲染")
    diagnostics = {
        "fetch_status": "success",
        "failure_reason": "",
        "comment_id": "",
        "content_kind": "article",
    }
    try:
        cache_paths = write_source_cache(url, article=article, source_html=source_html, diagnostics=diagnostics)
        diagnostics.update(cache_paths)
    except RuntimeError:
        pass
    diagnostics["cache_hit"] = False
    return source_type, article, source_html, diagnostics


def inspect_wechat_source_html(source_html: str) -> dict[str, Any]:
    soup = BeautifulSoup(source_html or "", "html.parser")
    error_text = _extract_wechat_error_text(soup)
    comment_id = _extract_comment_id(source_html)
    content_kind = _extract_content_kind(source_html)
    if error_text:
        lowered = error_text.lower()
        if "已被发布者删除" in error_text or "deleted by the author" in lowered:
            return {
                "fetch_status": "deleted",
                "failure_reason": error_text,
                "comment_id": comment_id,
                "content_kind": content_kind,
            }
        if any(token in error_text for token in ("无法查看", "已停止访问", "违规", "投诉", "封禁", "屏蔽")):
            return {
                "fetch_status": "blocked",
                "failure_reason": error_text,
                "comment_id": comment_id,
                "content_kind": content_kind,
            }
        return {
            "fetch_status": "invalid",
            "failure_reason": error_text,
            "comment_id": comment_id,
            "content_kind": content_kind,
        }
    if not _has_wechat_article_content(soup):
        return {
            "fetch_status": "parse_error",
            "failure_reason": "未识别到公众号文章正文节点",
            "comment_id": comment_id,
            "content_kind": content_kind,
        }
    return {
        "fetch_status": "success",
        "failure_reason": "",
        "comment_id": comment_id,
        "content_kind": content_kind,
    }


def _build_readable_article(*, source_html: str, original_url: str) -> ArticleData:
    document = Document(source_html)
    content_html = document.summary(html_partial=True)
    content_soup = BeautifulSoup(content_html, "html.parser")
    source_soup = BeautifulSoup(source_html, "html.parser")
    title = _extract_title(document, source_soup)
    author = _extract_author(source_soup)
    return ArticleData(
        title=title,
        author=author,
        account_name="",
        content_html=str(content_soup),
        original_url=original_url,
    )


def _looks_like_unusable_article(article: ArticleData) -> bool:
    title = str(article.title or "").strip().lower()
    text = BeautifulSoup(article.content_html or "", "html.parser").get_text("\n", strip=True)
    if title in {"", "[no-title]", "untitled"} and not text:
        return True
    return len(text) < 20 and not str(article.author or "").strip()


def _read_response_text(response: Any) -> str:
    encoding = getattr(response, "encoding", None)
    if encoding in (None, "", "ISO-8859-1"):
        setattr(response, "encoding", "utf-8")
    return str(response.text)


def _extract_host(url: str) -> str:
    parsed = urlparse(str(url).strip())
    return str(parsed.hostname or "").strip().lower()


def _extract_title(document: Document, source_soup: BeautifulSoup) -> str:
    heading = source_soup.find("h1")
    if heading and heading.get_text(strip=True):
        return heading.get_text(strip=True)
    title = str(document.title() or "").strip()
    if title:
        return title
    html_title = source_soup.title.get_text(strip=True) if source_soup.title else ""
    return html_title or "未命名文章"


def _extract_author(source_soup: BeautifulSoup) -> str:
    meta = source_soup.find("meta", attrs={"name": "author"})
    if meta and meta.get("content"):
        return str(meta.get("content")).strip()
    for selector in ('meta[property="article:author"]', '[itemprop="author"]', ".author", ".ArticleAuthor-name"):
        node = source_soup.select_one(selector)
        if node:
            if node.name == "meta" and node.get("content"):
                return str(node.get("content")).strip()
            text = node.get_text(strip=True)
            if text:
                return text
    return ""


def _extract_wechat_error_text(source_soup: BeautifulSoup) -> str:
    title = source_soup.select_one(".weui-msg__title")
    if title and title.get_text(strip=True):
        return title.get_text(" ", strip=True)
    block = source_soup.select_one(".mesg-block")
    if block and block.get_text(strip=True):
        return block.get_text(" ", strip=True)
    return ""


def _has_wechat_article_content(source_soup: BeautifulSoup) -> bool:
    if source_soup.select_one("#js_article #js_content"):
        return True
    if source_soup.select_one("#js_content"):
        return True
    if source_soup.select_one("#img-content"):
        return True
    return False


def _extract_content_kind(source_html: str) -> str:
    match = re.search(r"item_show_type[\"'\s:=]+(\d+)", source_html or "", re.IGNORECASE)
    if not match:
        return "article"
    kind = int(match.group(1))
    if kind == 8:
        return "image_share"
    if kind == 10:
        return "text_share"
    if kind == 0:
        return "article"
    return "unknown"


def _extract_comment_id(source_html: str) -> str:
    patterns = [
        r"var comment_id = '(?P<comment_id>\d+)' \|\| '0';",
        r"comment_id:\s*JsDecode\('(?P<comment_id>\d+)'\)",
        r"d\.comment_id\s*=\s*xml \? getXmlValue\('comment_id\.DATA'\) : '(?P<comment_id>\d+)';",
        r"window\.comment_id\s*=\s*'(?P<comment_id>\d+)'",
    ]
    for pattern in patterns:
        match = re.search(pattern, source_html or "")
        if match:
            return str(match.groupdict().get("comment_id") or match.group(1) or "").strip()
    return ""
