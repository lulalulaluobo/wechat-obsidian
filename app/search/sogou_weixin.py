from __future__ import annotations

import math
import re
from collections.abc import Callable
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from app.search.providers import SearchProviderError, SearchResult
from app.wechat_sync import USER_AGENT


SOGOU_WEIXIN_SEARCH_URL = "https://weixin.sogou.com/weixin"
SOGOU_WEIXIN_BASE_URL = "https://weixin.sogou.com/"
SOGOU_HEADERS = {
    "User-Agent": USER_AGENT,
    "Referer": SOGOU_WEIXIN_BASE_URL,
}


def search_sogou_weixin(query: str, *, limit: int = 10) -> list[SearchResult]:
    normalized_query = str(query or "").strip()
    if not normalized_query:
        raise SearchProviderError("搜索关键词不能为空")
    normalized_limit = max(1, min(50, int(limit or 10)))
    session = requests.Session()
    results: list[SearchResult] = []
    seen_urls: set[str] = set()
    max_pages = max(1, math.ceil(normalized_limit / 10))
    for page in range(1, max_pages + 1):
        try:
            response = session.get(
                SOGOU_WEIXIN_SEARCH_URL,
                params={"type": "2", "query": normalized_query, "ie": "utf8", "page": page},
                headers=SOGOU_HEADERS,
                timeout=12,
            )
            response.raise_for_status()
        except Exception as error:
            raise SearchProviderError(f"搜狗微信搜索失败：{error}") from error

        _approve_search_page(session, response.text)
        page_results = parse_sogou_weixin_results(
            response.text,
            limit=10,
            link_resolver=lambda href, referer=response.url: _resolve_sogou_link_url(session, href, referer=referer),
        )
        if not page_results:
            break
        for item in page_results:
            url = str(item.get("url") or "").strip()
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)
            results.append(item)
            if len(results) >= normalized_limit:
                return results
    return results


def parse_sogou_weixin_results(
    html: str,
    *,
    limit: int = 10,
    link_resolver: Callable[[str], str] | None = None,
) -> list[SearchResult]:
    soup = BeautifulSoup(html or "", "html.parser")
    results: list[SearchResult] = []
    seen: set[str] = set()
    for item in soup.select("ul.news-list > li, .news-list li"):
        link = item.select_one("h3 a[href]") or item.select_one("a[href]")
        if link is None:
            continue
        raw_url = str(link.get("href") or "")
        url = _normalize_result_url(raw_url)
        if (not url or "mp.weixin.qq.com" not in url) and link_resolver is not None:
            url = _normalize_result_url(link_resolver(raw_url))
        if not url or "mp.weixin.qq.com" not in url or url in seen:
            continue
        seen.add(url)
        title = " ".join(link.get_text("", strip=False).split())
        snippet_el = item.select_one(".txt-info") or item.select_one("p")
        source_el = item.select_one(".account") or item.select_one(".s-p a")
        published_el = item.select_one(".s2") or item.select_one("[t]")
        results.append(
            {
                "title": title,
                "url": url,
                "source_name": source_el.get_text(" ", strip=True) if source_el else "",
                "published_at": published_el.get_text(" ", strip=True) if published_el else "",
                "snippet": snippet_el.get_text(" ", strip=True) if snippet_el else "",
                "provider": "sogou_weixin",
                "already_ingested": False,
                "score": None,
            }
        )
        if len(results) >= max(int(limit or 10), 1):
            break
    return results


def _approve_search_page(session: requests.Session, html: str) -> None:
    uuid_match = re.search(r'var\s+uuid\s*=\s*"([^"]+)"', html or "")
    token_match = re.search(r'var\s+ssToken\s*=\s*"([^"]+)"', html or "")
    if not uuid_match or not token_match:
        return
    try:
        session.get(
            f"{SOGOU_WEIXIN_BASE_URL.rstrip('/')}/approve",
            params={"uuid": uuid_match.group(1), "token": token_match.group(1), "from": "search"},
            headers=SOGOU_HEADERS,
            timeout=8,
        )
    except Exception:
        return


def _resolve_sogou_link_url(session: requests.Session, raw_url: str, *, referer: str) -> str:
    url = urljoin(SOGOU_WEIXIN_BASE_URL, str(raw_url or "").strip())
    parsed = urlparse(url)
    if not parsed.netloc.endswith("sogou.com") or not parsed.path.endswith("/link"):
        return url
    try:
        response = session.get(
            url,
            headers={**SOGOU_HEADERS, "Referer": referer or SOGOU_WEIXIN_SEARCH_URL},
            allow_redirects=False,
            timeout=8,
        )
    except Exception:
        return ""
    location = str(response.headers.get("location") or "").strip()
    if "mp.weixin.qq.com" in location:
        return location
    return _extract_js_redirect_url(response.text)


def _extract_js_redirect_url(html: str) -> str:
    segments = re.findall(r"url\s*\+=\s*'([^']*)'", html or "")
    if not segments:
        return ""
    url = "".join(segments).replace("@", "")
    return url if "mp.weixin.qq.com" in url else ""


def _normalize_result_url(raw_url: str) -> str:
    url = unquote(str(raw_url or "").strip())
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.netloc.endswith("sogou.com") and parsed.path.endswith("/link"):
        query = parse_qs(parsed.query)
        candidate = (query.get("url") or [""])[0]
        if candidate:
            url = unquote(candidate)
    if url.startswith("//"):
        url = f"https:{url}"
    return url
