from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from readability import Document

from app.core.pipeline import ArticleData, WeChatArticlePipeline


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
    host = urlparse(str(url).strip()).netloc.lower()
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
) -> tuple[str, ArticleData, str]:
    source_type = detect_source_type(url)
    if source_type == "wechat":
        return _fetch_wechat_article(url, timeout=timeout, http_session=http_session)
    return _fetch_readable_article(url, source_type=source_type, timeout=timeout, http_session=http_session)


def _fetch_wechat_article(
    url: str,
    *,
    timeout: int,
    http_session=None,
) -> tuple[str, ArticleData, str]:
    pipeline = WeChatArticlePipeline(timeout=timeout)
    if http_session is not None:
        pipeline.session = http_session
    if not pipeline.validate_url(url):
        raise ValueError("无效的微信文章链接，仅支持 mp.weixin.qq.com 或 weixin.qq.com")
    source_html = pipeline.fetch_html(url)
    article = pipeline.extract_article(source_html, url)
    return "wechat", article, source_html


def _fetch_readable_article(
    url: str,
    *,
    source_type: str,
    timeout: int,
    http_session=None,
) -> tuple[str, ArticleData, str]:
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
    return source_type, article, source_html


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
