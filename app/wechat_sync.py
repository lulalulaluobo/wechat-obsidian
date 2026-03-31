from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time, timezone
from typing import Any

import requests

from app.config import get_settings


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class SyncRange:
    start_ts: int
    end_ts: int
    start_date: str
    end_date: str


class WechatMPClient:
    def __init__(self, http_session=None) -> None:
        settings = get_settings()
        self.token = str(settings.wechat_mp_token or "").strip()
        self.cookie = str(settings.wechat_mp_cookie or "").strip()
        self.timeout = max(settings.default_timeout, 20)
        self.session = http_session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Referer": "https://mp.weixin.qq.com/",
                "Origin": "https://mp.weixin.qq.com",
                "Accept": "application/json, text/plain, */*",
                "Cookie": self.cookie,
            }
        )

    @property
    def configured(self) -> bool:
        return bool(self.token and self.cookie)

    def _ensure_configured(self) -> None:
        if not self.configured:
            raise RuntimeError("公众号后台 token / cookie 尚未配置完整")

    def _request(self, endpoint: str, *, params: dict[str, Any]) -> dict[str, Any]:
        self._ensure_configured()
        response = self.session.get(endpoint, params=params, timeout=self.timeout, allow_redirects=True)
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError as error:
            raise RuntimeError("公众号后台返回了无法解析的 JSON") from error
        base_resp = payload.get("base_resp") if isinstance(payload, dict) else None
        if isinstance(base_resp, dict) and int(base_resp.get("ret", 0) or 0) not in {0}:
            raise RuntimeError(str(base_resp.get("err_msg") or "公众号后台请求失败"))
        return payload

    def check_login_status(self) -> dict[str, Any]:
        self._ensure_configured()
        response = self.session.get(
            "https://mp.weixin.qq.com/cgi-bin/home",
            params={"t": "home/index", "lang": "zh_CN", "token": self.token},
            timeout=self.timeout,
            allow_redirects=True,
        )
        response.raise_for_status()
        html = response.text
        if "登录" in html and "扫码" in html and "token=" not in response.url:
            return {"configured": True, "valid": False, "message": "公众号后台登录信息已失效，请更新 token / cookie"}
        return {"configured": True, "valid": True, "message": "公众号后台登录信息可用"}

    def search_accounts(self, keyword: str, *, begin: int = 0, size: int = 5) -> dict[str, Any]:
        normalized_keyword = str(keyword or "").strip()
        if not normalized_keyword:
            raise ValueError("keyword 不能为空")
        payload = self._request(
            "https://mp.weixin.qq.com/cgi-bin/searchbiz",
            params={
                "action": "search_biz",
                "begin": int(begin or 0),
                "count": int(size or 5),
                "query": normalized_keyword,
                "token": self.token,
                "lang": "zh_CN",
                "f": "json",
                "ajax": "1",
            },
        )
        raw_items = payload.get("list") if isinstance(payload.get("list"), list) else []
        items: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            items.append(
                {
                    "fakeid": str(item.get("fakeid") or "").strip(),
                    "nickname": str(item.get("nickname") or "").strip(),
                    "alias": str(item.get("alias") or "").strip(),
                    "round_head_img": str(item.get("round_head_img") or "").strip(),
                    "service_type": int(item.get("service_type") or 0),
                    "signature": str(item.get("signature") or "").strip(),
                }
            )
        return {"items": items, "total": int(payload.get("total") or len(items))}

    def fetch_articles(self, fakeid: str, *, begin: int = 0, size: int = 5, keyword: str = "") -> dict[str, Any]:
        normalized_fakeid = str(fakeid or "").strip()
        if not normalized_fakeid:
            raise ValueError("fakeid 不能为空")
        normalized_keyword = str(keyword or "").strip()
        is_search = bool(normalized_keyword)
        payload = self._request(
            "https://mp.weixin.qq.com/cgi-bin/appmsgpublish",
            params={
                "sub": "search" if is_search else "list",
                "search_field": "7" if is_search else "null",
                "begin": int(begin or 0),
                "count": int(size or 5),
                "query": normalized_keyword,
                "fakeid": normalized_fakeid,
                "type": "101_1",
                "free_publish_type": 1,
                "sub_action": "list_ex",
                "token": self.token,
                "lang": "zh_CN",
                "f": "json",
                "ajax": 1,
            },
        )
        publish_page_raw = str(payload.get("publish_page") or "").strip()
        if not publish_page_raw:
            return {"items": [], "total": 0}
        publish_page = json.loads(publish_page_raw)
        raw_list = publish_page.get("publish_list") if isinstance(publish_page.get("publish_list"), list) else []
        items: list[dict[str, Any]] = []
        for entry in raw_list:
            if not isinstance(entry, dict) or not entry.get("publish_info"):
                continue
            try:
                publish_info = json.loads(str(entry.get("publish_info") or ""))
            except json.JSONDecodeError:
                continue
            for article in publish_info.get("appmsgex", []) or []:
                if not isinstance(article, dict):
                    continue
                items.append(
                    {
                        "aid": str(article.get("aid") or "").strip(),
                        "title": str(article.get("title") or "").strip(),
                        "article_url": str(article.get("link") or "").strip(),
                        "author": str(article.get("author_name") or "").strip(),
                        "digest": str(article.get("digest") or "").strip(),
                        "cover": str(
                            article.get("pic_cdn_url_235_1")
                            or article.get("pic_cdn_url_16_9")
                            or article.get("cover")
                            or ""
                        ).strip(),
                        "publish_time": int(article.get("update_time") or 0),
                        "create_time": int(article.get("create_time") or 0),
                        "content_kind": _map_content_kind(article.get("item_show_type")),
                    }
                )
        return {"items": items, "total": int(publish_page.get("total_count") or len(items))}


def _map_content_kind(item_show_type: Any) -> str:
    normalized = int(item_show_type or 0)
    if normalized == 8:
        return "image_share"
    if normalized == 10:
        return "text_share"
    if normalized == 0:
        return "article"
    return "unknown"


def parse_sync_range(start_date: str, end_date: str) -> SyncRange:
    normalized_start = str(start_date or "").strip()
    normalized_end = str(end_date or "").strip()
    if not normalized_start or not normalized_end:
        raise ValueError("首次同步必须显式提供开始和结束日期")
    start_dt = datetime.fromisoformat(normalized_start).replace(tzinfo=timezone.utc)
    end_dt = datetime.fromisoformat(normalized_end).replace(tzinfo=timezone.utc)
    if start_dt > end_dt:
        raise ValueError("开始日期不能晚于结束日期")
    start_at = datetime.combine(start_dt.date(), time.min, tzinfo=timezone.utc)
    end_at = datetime.combine(end_dt.date(), time.max, tzinfo=timezone.utc)
    return SyncRange(
        start_ts=int(start_at.timestamp()),
        end_ts=int(end_at.timestamp()),
        start_date=normalized_start,
        end_date=normalized_end,
    )
