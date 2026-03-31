#!/usr/bin/env python3
"""
Self-contained WeChat article pipeline.

Fetch a WeChat public article, extract the main content, download images,
convert the content to Markdown, format the Markdown, and save the final
article bundle to a numbered output folder.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import os
import re
import sys
import hmac
from datetime import datetime, timezone
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

import requests

try:
    from PIL import Image, UnidentifiedImageError
except ImportError:  # pragma: no cover - exercised only when dependency is absent
    Image = None
    UnidentifiedImageError = Exception


USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

INVALID_LINK_PATTERNS = (
    "javascript:",
    "#",
)

WECHAT_NOISE_LINES = (
    "预览时标签不可点",
    "继续滑动看下一个",
    "微信扫一扫关注该公众号",
    "轻触阅读原文",
)

PROMOTION_SECTION_KEYWORDS = (
    "福利领取",
    "编程实战小课",
    "交流群",
    "社群",
    "社区",
    "阅读更多",
    "推荐阅读",
    "相关阅读",
)

PROMOTION_LINE_PATTERNS = [
    re.compile(r'点个.*(?:关注|在看)', re.IGNORECASE),
    re.compile(r'私信回复', re.IGNORECASE),
    re.compile(r'(?:免费|直接).*领', re.IGNORECASE),
    re.compile(r'资料包', re.IGNORECASE),
    re.compile(r'扫码.*(?:进群|交流群|社群|社区)', re.IGNORECASE),
    re.compile(r'(?:加群|进群|交流群|社群|社区)', re.IGNORECASE),
    re.compile(r'商务合作', re.IGNORECASE),
    re.compile(r'联系我', re.IGNORECASE),
    re.compile(r'微信号|微信：|VX[:：]?\s*|vx[:：]?\s*|V信', re.IGNORECASE),
    re.compile(r'瓜哥只分享', re.IGNORECASE),
]

AUTHOR_INTRO_PATTERNS = [
    re.compile(r'^>?\s*大家好[！!，,。.\s]*我是'),
    re.compile(r'^>?\s*我是.+(?:前|曾|现在|目前).*(?:副总裁|创始人|带队|死磕|专注)'),
]

STATIC_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
PASSTHROUGH_IMAGE_EXTENSIONS = {'.gif', '.svg'}
IMAGE_MAX_DIMENSION = 1600
IMAGE_WEBP_QUALITY = 78
DEFAULT_IMAGE_MODE = 'wechat_hotlink'


@dataclass
class ArticleData:
    title: str
    author: str
    account_name: str
    content_html: str
    original_url: str


@dataclass
class S3UploadConfig:
    access_key_id: str
    secret_access_key: str
    endpoint: str
    region: str
    bucket_name: str
    path_template: str
    public_base_url: str


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', '', name)
    name = re.sub(r'\s+', '_', name)
    name = re.sub(r'_+', '_', name).strip('_')
    return (name[:100] or 'untitled').strip('_') or 'untitled'


def get_next_folder_number(base_dir: Path) -> int:
    if not base_dir.exists():
        return 1

    max_num = 0
    for child in base_dir.iterdir():
        if not child.is_dir():
            continue
        match = re.match(r'^(\d+)_', child.name)
        if match:
            max_num = max(max_num, int(match.group(1)))
    return max_num + 1


def strip_tags(value: str) -> str:
    value = re.sub(r'<[^>]+>', '', value)
    return html.unescape(value).strip()


def normalize_inline_text(value: str) -> str:
    value = value.replace('\xa0', ' ')
    value = re.sub(r'\s+', ' ', value)
    return value.strip()


def _normalize_heading_candidate(value: str) -> str:
    normalized = normalize_inline_text(value)
    normalized = re.sub(r'^(?:#{1,6}\s*)?', '', normalized)
    normalized = re.sub(r'^[\W_]+', '', normalized)
    return normalized.strip()


def _is_wechat_noise_line(line: str) -> bool:
    normalized = normalize_inline_text(line)
    if not normalized:
        return False
    if normalized in WECHAT_NOISE_LINES:
        return True
    return (
        normalized.startswith('原创')
        and '在小说阅读器中沉浸阅读' in normalized
    ) or normalized.startswith('以下文章来源于') or normalized.startswith('作者 | ')


def _is_promotion_section_heading(line: str) -> bool:
    normalized = _normalize_heading_candidate(line)
    if not normalized:
        return False
    compact = re.sub(r'\s+', '', normalized).casefold()
    return any(keyword.casefold() in compact for keyword in PROMOTION_SECTION_KEYWORDS)


class WeChatArticlePipeline:
    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(self._build_headers())

    def _build_headers(self) -> Dict[str, str]:
        return {
            'User-Agent': USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Cache-Control': 'no-cache',
            'Pragma': 'no-cache',
            'Upgrade-Insecure-Requests': '1',
        }

    def validate_url(self, url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme in {'http', 'https'} and (
            'mp.weixin.qq.com' in parsed.netloc or 'weixin.qq.com' in parsed.netloc
        )

    def fetch_html(self, url: str) -> str:
        response = self.session.get(url, timeout=self.timeout, allow_redirects=True)
        response.raise_for_status()
        if response.encoding in (None, 'ISO-8859-1'):
            response.encoding = 'utf-8'
        return response.text

    def extract_article(self, source_html: str, original_url: str) -> ArticleData:
        title = self._extract_first_match(
            source_html,
            [
                r'id="activity-name"[^>]*>\s*<span[^>]*>(.*?)</span>',
                r'class="rich_media_title[^"]*"[^>]*>(.*?)</h1>',
                r'<h1[^>]*>(.*?)</h1>',
                r'<title>(.*?)</title>',
            ],
        ) or '未命名文章'

        author = self._extract_first_match(
            source_html,
            [
                r'id="js_author_name_text"[^>]*>(.*?)</span>',
                r'id="js_author_name"[^>]*>(.*?)</span>',
                r'id="js_name"[^>]*>(.*?)</a>',
                r'class="profile_nickname[^"]*"[^>]*>(.*?)</span>',
            ],
        ) or ''

        account_name = self._extract_first_match(
            source_html,
            [
                r'class="profile_nickname[^"]*"[^>]*>(.*?)</span>',
                r'id="js_name"[^>]*>(.*?)</a>',
            ],
        ) or ''

        content_html = self._extract_content_html(source_html)
        content_html = self._clean_html(content_html)

        return ArticleData(
            title=title,
            author=author,
            account_name=account_name,
            content_html=content_html,
            original_url=original_url,
        )

    def _extract_first_match(self, content: str, patterns: List[str]) -> str:
        for pattern in patterns:
            match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
            if match:
                value = strip_tags(match.group(1))
                value = re.sub(r'\s*[-_|]\s*微信.*$', '', value)
                if value:
                    return value
        return ''

    def _extract_content_html(self, source_html: str) -> str:
        patterns = [
            r'id="js_content"[^>]*>(.*?)</div>\s*</div>\s*<div[^>]*class="rich_media_tool',
            r'id="img-content"[^>]*>(.*?)<div[^>]*id="js_pc_qr_code"',
            r'id="img-content"[^>]*>(.*?)$',
            r'<body[^>]*>(.*?)</body>',
        ]
        for pattern in patterns:
            match = re.search(pattern, source_html, re.DOTALL | re.IGNORECASE)
            if match:
                return match.group(1)
        return source_html

    def _clean_html(self, content_html: str) -> str:
        content_html = re.sub(
            r'<script[^>]*>.*?</script>',
            '',
            content_html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        content_html = re.sub(
            r'<style[^>]*>.*?</style>',
            '',
            content_html,
            flags=re.DOTALL | re.IGNORECASE,
        )
        content_html = re.sub(r'<!--.*?-->', '', content_html, flags=re.DOTALL)

        def replace_lazy_image(match: re.Match[str]) -> str:
            image_tag = match.group(0)
            source_match = re.search(
                r'(?:data-src|data-original|src)=["\']([^"\']+)["\']',
                image_tag,
                flags=re.IGNORECASE,
            )
            if not source_match:
                return image_tag
            src = html.unescape(source_match.group(1))
            if ' src=' in image_tag:
                image_tag = re.sub(r'src=["\'][^"\']+["\']', f'src="{src}"', image_tag, count=1)
            else:
                image_tag = re.sub(r'<img', f'<img src="{src}"', image_tag, count=1)
            return image_tag

        content_html = re.sub(r'<img[^>]*>', replace_lazy_image, content_html, flags=re.IGNORECASE)
        content_html = re.sub(
            r'<img[^>]*(?:height=["\']?1["\']?[^>]*width=["\']?1["\']?|width=["\']?1["\']?[^>]*height=["\']?1["\']?)[^>]*>',
            '',
            content_html,
            flags=re.IGNORECASE,
        )
        content_html = re.sub(r'<div[^>]*>\s*</div>', '', content_html, flags=re.IGNORECASE)
        content_html = re.sub(r'\n\s*\n\s*\n+', '\n\n', content_html)
        return content_html.strip()

    def build_clean_html(self, article: ArticleData) -> str:
        meta_parts = []
        if article.author:
            meta_parts.append(f'<span>作者: {html.escape(article.author)}</span>')
        meta_html = ' | '.join(meta_parts)

        return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(article.title)}</title>
</head>
<body>
    <article>
        <h1>{html.escape(article.title)}</h1>
        <p>{meta_html}</p>
        <div class="article-content">
{article.content_html}
        </div>
        <p>原文链接: <a href="{html.escape(article.original_url)}">{html.escape(article.original_url)}</a></p>
    </article>
</body>
</html>
'''


def load_s3_upload_config() -> S3UploadConfig:
    required_fields = {
        'access_key_id': (os.environ.get('WECHAT_MD_IMAGE_STORAGE_ACCESS_KEY_ID') or '').strip(),
        'secret_access_key': (os.environ.get('WECHAT_MD_IMAGE_STORAGE_SECRET_ACCESS_KEY') or '').strip(),
        'endpoint': (os.environ.get('WECHAT_MD_IMAGE_STORAGE_ENDPOINT') or '').strip(),
        'region': (os.environ.get('WECHAT_MD_IMAGE_STORAGE_REGION') or '').strip(),
        'bucket': (os.environ.get('WECHAT_MD_IMAGE_STORAGE_BUCKET') or '').strip(),
        'path_template': (os.environ.get('WECHAT_MD_IMAGE_STORAGE_PATH_TEMPLATE') or '').strip(),
        'public_base_url': (os.environ.get('WECHAT_MD_IMAGE_STORAGE_PUBLIC_BASE_URL') or '').strip(),
    }
    missing = [name for name, value in required_fields.items() if not value]
    if missing:
        raise RuntimeError(
            'S3 图床配置不完整，缺少字段: '
            + ', '.join(missing)
        )

    return S3UploadConfig(
        access_key_id=required_fields['access_key_id'],
        secret_access_key=required_fields['secret_access_key'],
        endpoint=required_fields['endpoint'].rstrip('/'),
        region=required_fields['region'],
        bucket_name=required_fields['bucket'],
        path_template=required_fields['path_template'],
        public_base_url=required_fields['public_base_url'].rstrip('/'),
    )


def _sha256_hexdigest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _hmac_sha256(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode('utf-8'), hashlib.sha256).digest()


def _build_signing_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    date_key = _hmac_sha256(('AWS4' + secret_key).encode('utf-8'), date_stamp)
    region_key = _hmac_sha256(date_key, region)
    service_key = _hmac_sha256(region_key, service)
    return _hmac_sha256(service_key, 'aws4_request')


def _format_bytes(size: int) -> str:
    if size < 1024:
        return f'{size} B'
    if size < 1024 * 1024:
        return f'{size / 1024:.1f} KB'
    return f'{size / 1024 / 1024:.2f} MB'


class S3Uploader:
    def __init__(
        self,
        config: S3UploadConfig,
        timeout: int,
        http_session: Optional[requests.Session] = None,
    ) -> None:
        self.config = config
        self.timeout = timeout
        self.http_session = http_session or requests.Session()
        parsed = urlparse(config.endpoint)
        self.endpoint_base = f'{parsed.scheme}://{parsed.netloc}'
        self.host = parsed.netloc

    def upload(self, filename: str, content: bytes, content_type: str) -> str:
        now = datetime.now(timezone.utc)
        object_key = self._build_object_key(filename, content, now)
        self._send_signed_request('PUT', object_key, content=content, content_type=content_type)
        return f'{self.config.public_base_url}/{quote(object_key, safe="/")}'

    def delete(self, remote_url: str) -> None:
        object_key = self._extract_object_key_from_remote_url(remote_url)
        self._send_signed_request('DELETE', object_key, content=b'')

    def _build_object_key(self, filename: str, content: bytes, now: datetime) -> str:
        path = self.config.path_template or '{filename}'
        replacements = {
            '{year}': now.strftime('%Y'),
            '{mon}': now.strftime('%m'),
            '{day}': now.strftime('%d'),
            '{filename}': filename,
            '{md5}': hashlib.md5(content).hexdigest(),
        }
        for key, value in replacements.items():
            path = path.replace(key, value)
        return path.lstrip('/')

    def _extract_object_key_from_remote_url(self, remote_url: str) -> str:
        remote_prefix = f'{self.config.public_base_url}/'
        if not remote_url.startswith(remote_prefix):
            raise ValueError(f'URL 不属于当前图床域名: {remote_url}')
        return remote_url[len(remote_prefix):]

    def _send_signed_request(
        self,
        method: str,
        object_key: str,
        content: bytes,
        content_type: Optional[str] = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        date_stamp = now.strftime('%Y%m%d')
        amz_date = now.strftime('%Y%m%dT%H%M%SZ')
        canonical_uri = f'/{self.config.bucket_name}/{quote(object_key, safe="/")}'
        payload_hash = _sha256_hexdigest(content)
        signed_headers_list = ['host', 'x-amz-content-sha256', 'x-amz-date']
        canonical_headers = (
            f'host:{self.host}\n'
            f'x-amz-content-sha256:{payload_hash}\n'
            f'x-amz-date:{amz_date}\n'
        )
        headers = {
            'Host': self.host,
            'X-Amz-Content-Sha256': payload_hash,
            'X-Amz-Date': amz_date,
        }
        if content_type:
            signed_headers_list.insert(0, 'content-type')
            canonical_headers = f'content-type:{content_type}\n' + canonical_headers
            headers['Content-Type'] = content_type

        signed_headers = ';'.join(signed_headers_list)
        canonical_request = '\n'.join(
            [
                method,
                canonical_uri,
                '',
                canonical_headers,
                signed_headers,
                payload_hash,
            ]
        )
        credential_scope = f'{date_stamp}/{self.config.region}/s3/aws4_request'
        string_to_sign = '\n'.join(
            [
                'AWS4-HMAC-SHA256',
                amz_date,
                credential_scope,
                _sha256_hexdigest(canonical_request.encode('utf-8')),
            ]
        )
        signing_key = _build_signing_key(self.config.secret_access_key, date_stamp, self.config.region, 's3')
        signature = hmac.new(signing_key, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()
        headers['Authorization'] = (
            'AWS4-HMAC-SHA256 '
            f'Credential={self.config.access_key_id}/{credential_scope}, '
            f'SignedHeaders={signed_headers}, Signature={signature}'
        )
        request_url = f'{self.endpoint_base}{canonical_uri}'
        response = self.http_session.request(
            method,
            request_url,
            headers=headers,
            data=content,
            timeout=self.timeout,
        )
        response.raise_for_status()


class MarkdownImageDownloader:
    def __init__(
        self,
        output_dir: Path,
        base_url: Optional[str],
        timeout: int,
        image_mode: str = DEFAULT_IMAGE_MODE,
        uploader: Optional[S3Uploader] = None,
        http_session: Optional[requests.Session] = None,
    ) -> None:
        self.output_dir = output_dir
        self.base_url = base_url
        self.timeout = timeout
        self.image_mode = image_mode
        self.image_index = 0
        self.uploader = uploader
        self.http_session = http_session or requests.Session()
        self.summary: Dict[str, object] = {
            'original_bytes': 0,
            'compressed_bytes': 0,
            'saved_bytes': 0,
            'saved_ratio': 0.0,
            'uploaded_images': 0,
            'gif_passthrough_images': 0,
            'fallback_original_url_images': 0,
            'wechat_hotlink_images': 0,
            'deleted_unused_uploads': 0,
        }
        self.uploaded_urls: List[str] = []

    def download(self, source: str) -> Optional[str]:
        source = html.unescape(source).split('#', 1)[0]
        if not source or source.startswith('data:'):
            return None

        if self.base_url and not source.startswith(('http://', 'https://')):
            source = urljoin(self.base_url, source)

        self.image_index += 1
        if self.image_mode == 'wechat_hotlink':
            self.summary['wechat_hotlink_images'] = int(self.summary['wechat_hotlink_images']) + 1
            return source

        extension = self._detect_extension(source)
        if extension in PASSTHROUGH_IMAGE_EXTENSIONS:
            if extension == '.gif':
                self.summary['gif_passthrough_images'] = int(self.summary['gif_passthrough_images']) + 1
            else:
                self.summary['fallback_original_url_images'] = int(self.summary['fallback_original_url_images']) + 1
            return source

        headers = {
            'User-Agent': USER_AGENT,
            'Accept': 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        }
        if 'mmbiz.qpic.cn' in source or 'weixin' in source:
            headers['Referer'] = 'https://mp.weixin.qq.com/'
            headers['Origin'] = 'https://mp.weixin.qq.com'

        try:
            response = self.http_session.get(source, headers=headers, timeout=self.timeout)
            response.raise_for_status()
            source_content = response.content
            if len(source_content) < 100:
                self._record_original_url_fallback()
                return source
            compressed_content, content_type, file_name = self._compress_static_image(source_content)
            uploaded_url = self._get_uploader().upload(file_name, compressed_content, content_type)
            self.summary['original_bytes'] = int(self.summary['original_bytes']) + len(source_content)
            self.summary['compressed_bytes'] = int(self.summary['compressed_bytes']) + len(compressed_content)
            self.summary['uploaded_images'] = int(self.summary['uploaded_images']) + 1
            self.uploaded_urls.append(uploaded_url)
            self._refresh_saved_stats()
            return uploaded_url
        except requests.RequestException:
            self._record_original_url_fallback()
            return source
        except (RuntimeError, OSError, ValueError, UnidentifiedImageError):
            self._record_original_url_fallback()
            return source

    def get_summary(self) -> Dict[str, object]:
        self._refresh_saved_stats()
        return dict(self.summary)

    def cleanup_unused_uploads(self, markdown: str) -> None:
        if not self.uploaded_urls:
            return
        referenced_urls = set(
            re.findall(r'!\[[^\]]*\]\((https?://[^)]+)\)', markdown)
        )
        if not referenced_urls:
            referenced_urls = set()
        for uploaded_url in list(self.uploaded_urls):
            if uploaded_url in referenced_urls:
                continue
            try:
                self._get_uploader().delete(uploaded_url)
            except (requests.RequestException, RuntimeError, ValueError):
                continue
            self.uploaded_urls.remove(uploaded_url)
            self.summary['deleted_unused_uploads'] = int(self.summary['deleted_unused_uploads']) + 1

    def _get_uploader(self) -> S3Uploader:
        if self.uploader is None:
            self.uploader = S3Uploader(
                config=load_s3_upload_config(),
                timeout=self.timeout,
                http_session=self.http_session,
            )
        return self.uploader

    def _compress_static_image(self, source_content: bytes) -> tuple[bytes, str, str]:
        if Image is None:
            raise RuntimeError('缺少 Pillow 依赖，请先执行: pip install Pillow')

        with Image.open(io.BytesIO(source_content)) as image:
            image.load()
            resample = getattr(Image, 'Resampling', Image).LANCZOS
            if max(image.size) > IMAGE_MAX_DIMENSION:
                image.thumbnail((IMAGE_MAX_DIMENSION, IMAGE_MAX_DIMENSION), resample)
            if image.mode not in {'RGB', 'RGBA'}:
                if 'A' in image.getbands():
                    image = image.convert('RGBA')
                else:
                    image = image.convert('RGB')

            buffer = io.BytesIO()
            image.save(
                buffer,
                format='WEBP',
                quality=IMAGE_WEBP_QUALITY,
                method=6,
            )
            compressed_content = buffer.getvalue()

        file_name = f'{hashlib.md5(compressed_content).hexdigest()}.webp'
        return compressed_content, 'image/webp', file_name

    def _record_original_url_fallback(self) -> None:
        self.summary['fallback_original_url_images'] = int(self.summary['fallback_original_url_images']) + 1

    def _refresh_saved_stats(self) -> None:
        original_bytes = int(self.summary['original_bytes'])
        compressed_bytes = int(self.summary['compressed_bytes'])
        saved_bytes = original_bytes - compressed_bytes
        saved_ratio = round((saved_bytes / original_bytes) * 100, 2) if original_bytes else 0.0
        self.summary['saved_bytes'] = saved_bytes
        self.summary['saved_ratio'] = saved_ratio

    def _detect_extension(self, source: str) -> str:
        parsed = urlparse(source)
        query = parsed.query.lower()
        for fmt, extension in [
            ('png', '.png'),
            ('gif', '.gif'),
            ('webp', '.webp'),
            ('jpeg', '.jpg'),
            ('jpg', '.jpg'),
        ]:
            if f'wx_fmt={fmt}' in query:
                return extension

        lower_path = parsed.path.lower()
        for extension in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'):
            if lower_path.endswith(extension):
                return extension if extension != '.jpeg' else '.jpg'
        return '.jpg'


class HTMLToMarkdownParser(HTMLParser):
    def __init__(self, image_downloader: MarkdownImageDownloader) -> None:
        super().__init__()
        self.image_downloader = image_downloader
        self.result: List[str] = []
        self.tag_stack: List[str] = []
        self.list_stack: List[str] = []
        self.list_counters: List[int] = []
        self.ignore_tags = {
            'script',
            'style',
            'nav',
            'footer',
            'header',
            'aside',
            'mat-icon',
            'code-block',
            'mp-style-type',
        }
        self.skip_depth = 0
        self.current_href: Optional[str] = None
        self.pending_newlines = 0
        self.in_pre = False

    def add_newlines(self, count: int) -> None:
        self.pending_newlines = max(self.pending_newlines, count)

    def flush_newlines(self) -> None:
        if self.pending_newlines > 0:
            self.result.append('\n' * self.pending_newlines)
            self.pending_newlines = 0

    def should_skip_subtree(self, tag: str, attrs_dict: Dict[str, str]) -> bool:
        if tag in self.ignore_tags:
            return True
        class_name = attrs_dict.get('class', '')
        if tag in {'ul', 'ol'} and 'code-snippet__line-index' in class_name:
            return True
        return False

    def handle_starttag(self, tag: str, attrs: List[tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        attrs_dict = {key: value or '' for key, value in attrs}

        if self.skip_depth > 0:
            self.skip_depth += 1
            return
        if self.should_skip_subtree(tag, attrs_dict):
            self.skip_depth = 1
            return

        parent_tag = self.tag_stack[-1] if self.tag_stack else None
        self.tag_stack.append(tag)
        if tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6'}:
            self.add_newlines(2)
            self.flush_newlines()
            level = int(tag[1])
            self.result.append('#' * level + ' ')
        elif tag == 'p':
            if parent_tag not in {'li', 'blockquote'}:
                self.add_newlines(2)
                self.flush_newlines()
        elif tag == 'br':
            self.result.append('  \n')
        elif tag == 'hr':
            self.flush_newlines()
            self.result.append('\n---\n')
        elif tag in {'strong', 'b'}:
            self.result.append('**')
        elif tag in {'em', 'i'}:
            self.result.append('*')
        elif tag == 'code' and not self.in_pre:
            self.result.append('`')
        elif tag == 'pre':
            self.add_newlines(2)
            self.flush_newlines()
            self.result.append('```text\n')
            self.in_pre = True
        elif tag == 'blockquote':
            self.add_newlines(2)
            self.flush_newlines()
            self.result.append('> ')
        elif tag == 'a':
            self.current_href = attrs_dict.get('href', '').strip()
            self.result.append('[')
        elif tag == 'img':
            self.flush_newlines()
            source = (
                attrs_dict.get('data-src')
                or attrs_dict.get('data-original')
                or attrs_dict.get('src')
                or ''
            )
            alt_text = attrs_dict.get('alt') or attrs_dict.get('data-alt') or 'image'
            local_path = self.image_downloader.download(source)
            target = local_path or source
            if target:
                self.result.append(f'![{alt_text}]({target})')
                self.add_newlines(2)
        elif tag == 'ul':
            self.add_newlines(2)
            self.flush_newlines()
            self.list_stack.append('ul')
        elif tag == 'ol':
            self.add_newlines(2)
            self.flush_newlines()
            self.list_stack.append('ol')
            self.list_counters.append(1)
        elif tag == 'li':
            self.flush_newlines()
            indent = '  ' * max(0, len(self.list_stack) - 1)
            if self.list_stack and self.list_stack[-1] == 'ol':
                index = self.list_counters[-1]
                self.result.append(f'{indent}{index}. ')
                self.list_counters[-1] += 1
            else:
                self.result.append(f'{indent}- ')
        elif tag == 'table':
            self.add_newlines(2)
            self.flush_newlines()
        elif tag == 'tr':
            self.result.append('|')
        elif tag in {'th', 'td'}:
            self.result.append(' ')

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()

        if self.skip_depth > 0:
            self.skip_depth -= 1
            return

        parent_tag = self.tag_stack[-2] if len(self.tag_stack) > 1 and self.tag_stack[-1] == tag else None
        if self.tag_stack and self.tag_stack[-1] == tag:
            self.tag_stack.pop()

        if tag in {'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'blockquote'}:
            self.add_newlines(2)
        elif tag == 'p':
            if parent_tag not in {'li', 'blockquote'}:
                self.add_newlines(2)
        elif tag in {'strong', 'b'}:
            self.result.append('**')
        elif tag in {'em', 'i'}:
            self.result.append('*')
        elif tag == 'code':
            if self.in_pre:
                if self.result and not self.result[-1].endswith('\n'):
                    self.result.append('\n')
            else:
                self.result.append('`')
        elif tag == 'pre':
            if self.result and self.result[-1].endswith('\n'):
                self.result.append('```')
            else:
                self.result.append('\n```')
            self.add_newlines(2)
            self.in_pre = False
        elif tag == 'a':
            href = (self.current_href or '').strip()
            if href:
                self.result.append(f']({href})')
            else:
                self.result.append(']')
            self.current_href = None
        elif tag == 'ul':
            if self.list_stack:
                self.list_stack.pop()
            self.add_newlines(2)
        elif tag == 'ol':
            if self.list_stack:
                self.list_stack.pop()
            if self.list_counters:
                self.list_counters.pop()
            self.add_newlines(2)
        elif tag == 'li':
            self.add_newlines(1)
        elif tag in {'th', 'td'}:
            self.result.append(' |')
        elif tag == 'tr':
            self.result.append('\n')

    def handle_data(self, data: str) -> None:
        if self.skip_depth > 0:
            return
        self.flush_newlines()
        if self.in_pre:
            normalized = data.replace('\xa0', ' ')
            if not normalized.strip():
                if '\n' in normalized or '\r' in normalized:
                    return
                self.result.append(normalized)
                return
            self.result.append(normalized)
            return
        normalized = data.replace('\xa0', ' ')
        if not normalized.strip():
            if '\n' in normalized or '\r' in normalized:
                return
            if normalized and (not self.result or not self.result[-1].endswith((' ', '\n'))):
                self.result.append(' ')
            return
        cleaned = re.sub(r'[ \t]+', ' ', normalized)
        cleaned = re.sub(r'\n\s*\n', '\n\n', cleaned)
        if cleaned.strip():
            self.result.append(cleaned)

    def get_markdown(self) -> str:
        content = ''.join(self.result)
        content = re.sub(r'\n{3,}', '\n\n', content)
        return content.strip() + '\n'


def format_markdown(markdown: str, markdown_dir: Path) -> tuple[str, Dict[str, object]]:
    summary: Dict[str, object] = {
        'removed_duplicate_headings': 0,
        'normalized_heading_levels': 0,
        'fixed_invalid_links': 0,
        'removed_missing_images': [],
        'removed_noise_lines': 0,
        'removed_promotion_blocks': 0,
        'removed_promotion_lines': 0,
        'removed_contact_lines': 0,
        'trimmed_blank_lines': 0,
    }

    text = markdown.replace('\r\n', '\n').replace('\r', '\n')
    text = text.replace('\xa0', ' ')
    text = _normalize_single_backtick_code_blocks(text)
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\*{4,}', '', text)
    text = re.sub(r'([^\n])(!\[[^\]]*\]\([^)]+\))', r'\1\n\n\2', text)
    text = re.sub(r'(!\[[^\]]*\]\([^)]+\))([^\n])', r'\1\n\n\2', text)

    def replace_invalid_link(match: re.Match[str]) -> str:
        label = normalize_inline_text(match.group(1))
        target = match.group(2).strip()
        lowered = target.lower()
        if lowered.startswith(INVALID_LINK_PATTERNS):
            summary['fixed_invalid_links'] = int(summary['fixed_invalid_links']) + 1
            return label
        return match.group(0)

    text = re.sub(r'\[([^\]]+?)\]\(([^)]+)\)', replace_invalid_link, text, flags=re.DOTALL)

    lines = text.split('\n')
    result_lines: List[str] = []
    previous_heading_text: Optional[str] = None
    previous_heading_level = 0
    in_code_block = False
    pending_heading_level: Optional[int] = None

    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()

        if stripped.startswith('```'):
            if in_code_block:
                line = '```'
            elif stripped == '```':
                line = '```text'
            in_code_block = not in_code_block
            result_lines.append(line)
            continue

        if in_code_block:
            result_lines.append(line)
            continue

        if pending_heading_level is not None:
            if stripped == '':
                continue
            line = '#' * pending_heading_level + ' ' + normalize_inline_text(stripped)
            stripped = line
            pending_heading_level = None

        if _is_wechat_noise_line(stripped):
            summary['removed_noise_lines'] = int(summary['removed_noise_lines']) + 1
            continue

        stripped = normalize_inline_text(stripped)
        line = normalize_inline_text(line)

        if not stripped:
            result_lines.append('')
            continue

        if _is_wechat_metadata_noise(stripped):
            summary['removed_noise_lines'] = int(summary['removed_noise_lines']) + 1
            continue

        heading_match = re.match(r'^(#{1,6})(?:\s+(.*))?$', stripped)
        if heading_match:
            level = len(heading_match.group(1))
            heading_text = normalize_inline_text(heading_match.group(2) or '')
            if not heading_text:
                pending_heading_level = level
                continue
            if heading_text and previous_heading_text == heading_text:
                summary['removed_duplicate_headings'] = int(summary['removed_duplicate_headings']) + 1
                continue
            if previous_heading_level and level > previous_heading_level + 1:
                level = previous_heading_level + 1
                summary['normalized_heading_levels'] = int(summary['normalized_heading_levels']) + 1
            previous_heading_level = level
            previous_heading_text = heading_text
            result_lines.append('#' * level + ' ' + heading_text)
            continue

        image_match = re.match(r'^!\[([^\]]*)\]\(([^)]+)\)$', stripped)
        if image_match:
            image_path = image_match.group(2).strip()
            if not image_path.startswith(('http://', 'https://')):
                candidate = (markdown_dir / image_path).resolve()
                if not candidate.exists():
                    cast_list = summary['removed_missing_images']
                    assert isinstance(cast_list, list)
                    cast_list.append(image_path)
                    continue
            alt = normalize_inline_text(image_match.group(1).strip())
            line = f'![{alt}]({image_path})'

        if re.match(r'^\s*[-*+]\s+', line):
            line = re.sub(r'^\s*[-*+]\s+', '- ', line)

        if stripped.startswith('>'):
            line = re.sub(r'^>\s*', '> ', stripped)

        if stripped == '***' or stripped == '___':
            line = '---'

        if '<' in line and '>' in line and not re.search(r'<https?://[^>]+>', line):
            line = re.sub(r'</?span[^>]*>', '', line)
            line = re.sub(r'</?font[^>]*>', '', line)
            line = re.sub(r'<br\s*/?>', '  ', line, flags=re.IGNORECASE)
            line = re.sub(r'<[^>]+>', '', line)

        line = re.sub(r'(\*\*[^*]+:\*\*)(?=\S)', r'\1 ', line)
        line = normalize_inline_text(line)
        if not line:
            result_lines.append('')
            continue

        result_lines.append(line)

    text = '\n'.join(result_lines)
    text = _insert_missing_table_separators(text)
    text = _normalize_blank_lines(text)
    text = _remove_promotional_content(text, summary)
    text = _normalize_blank_lines(text)
    summary['trimmed_blank_lines'] = max(0, markdown.count('\n\n\n') - text.count('\n\n\n'))
    return text.strip() + '\n', summary


def _normalize_blank_lines(markdown: str) -> str:
    lines = markdown.split('\n')
    normalized: List[str] = []
    blank_count = 0
    in_code_block = False

    for raw_line in lines:
        stripped = raw_line.strip()
        if stripped.startswith('```'):
            opening_fence = not in_code_block
            in_code_block = not in_code_block
            blank_count = 0
            if opening_fence and normalized and normalized[-1] != '':
                normalized.append('')
            normalized.append(raw_line.rstrip())
            continue

        if in_code_block:
            normalized.append(raw_line.rstrip())
            continue

        if stripped == '':
            blank_count += 1
            if blank_count <= 1:
                normalized.append('')
            continue

        blank_count = 0
        previous = normalized[-1] if normalized else None
        if stripped.startswith('#') or stripped.startswith('![') or stripped == '---':
            if previous not in (None, ''):
                normalized.append('')
        normalized.append(raw_line.rstrip())

    while normalized and normalized[-1] == '':
        normalized.pop()
    return '\n'.join(normalized)


def _normalize_single_backtick_code_blocks(markdown: str) -> str:
    lines = markdown.split('\n')
    normalized: List[str] = []
    index = 0

    while index < len(lines):
        inline_open_match = re.match(r'^(.*)`\s*$', lines[index])
        if inline_open_match and '```' not in lines[index] and lines[index].strip() != '`':
            prefix = inline_open_match.group(1).rstrip()
            probe_index = index + 1
            while probe_index < len(lines) and not lines[probe_index].strip():
                probe_index += 1

            end_index = probe_index
            block_lines: List[str] = []
            explicit_close = False
            while end_index < len(lines):
                current_line = lines[end_index]
                closing_match = re.match(r'^(.*)`\s*$', current_line)
                if closing_match and _looks_like_single_backtick_code_line(closing_match.group(1)):
                    closing_prefix = closing_match.group(1)
                    if closing_prefix:
                        block_lines.append(closing_prefix)
                    explicit_close = True
                    break
                if not current_line.strip():
                    break
                if not _looks_like_single_backtick_code_line(current_line):
                    break
                block_lines.append(current_line)
                end_index += 1
            if block_lines:
                block_text = '\n'.join(block_lines).strip('\n')
                fence_language = _detect_code_fence_language(block_text)
                if prefix:
                    normalized.append(prefix)
                    normalized.append('')
                normalized.append(f'```{fence_language}')
                normalized.extend(block_lines)
                normalized.append('```')
                index = end_index + 1 if explicit_close else end_index
                continue

            standalone_close_index = index + 1
            while standalone_close_index < len(lines):
                if lines[standalone_close_index].strip() == '`':
                    block_lines = _trim_blank_edge_lines(lines[index + 1:standalone_close_index])
                    if block_lines:
                        block_text = '\n'.join(block_lines).strip('\n')
                        fence_language = _detect_code_fence_language(block_text)
                        if prefix:
                            normalized.append(prefix)
                            normalized.append('')
                        normalized.append(f'```{fence_language}')
                        normalized.extend(block_lines)
                        normalized.append('```')
                        index = standalone_close_index + 1
                        break
                    break
                standalone_close_index += 1
            if index > probe_index:
                continue

        if lines[index].strip() == '`':
            end_index = index + 1
            while end_index < len(lines) and lines[end_index].strip() != '`':
                end_index += 1
            if end_index < len(lines):
                block_lines = lines[index + 1:end_index]
                block_text = '\n'.join(block_lines).strip('\n')
                fence_language = _detect_code_fence_language(block_text)
                normalized.append(f'```{fence_language}')
                normalized.extend(block_lines)
                normalized.append('```')
                index = end_index + 1
                continue
        normalized.append(lines[index])
        index += 1

    return '\n'.join(normalized)


def _trim_blank_edge_lines(lines: List[str]) -> List[str]:
    start = 0
    end = len(lines)
    while start < end and not lines[start].strip():
        start += 1
    while end > start and not lines[end - 1].strip():
        end -= 1
    return lines[start:end]


def _looks_like_single_backtick_code_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped == '---':
        return False
    if stripped.startswith(('#', '>')):
        return False
    if '->' in stripped:
        return True
    if stripped[0] in '{[}]':
        return True
    if stripped.startswith(('"', "'")):
        return True
    if stripped.startswith(('-y', '--')):
        return True
    if re.match(r'^[A-Za-z0-9_.-]+\s*:\s*', stripped):
        return True
    if re.match(r'^[A-Za-z0-9_.-]+\s*=\s*', stripped):
        return True
    if re.fullmatch(r'[A-Za-z0-9_./:-]+', stripped) and ('_' in stripped or '/' in stripped or '--' in stripped):
        return True
    return False


def _detect_code_fence_language(block_text: str) -> str:
    stripped = (block_text or '').strip()
    if stripped.startswith('{') or stripped.startswith('['):
        return 'json'
    return 'text'


def _insert_missing_table_separators(markdown: str) -> str:
    lines = markdown.split('\n')
    normalized: List[str] = []
    index = 0

    while index < len(lines):
        line = lines[index]
        if _is_markdown_table_row(line):
            table_lines: List[str] = []
            while index < len(lines) and _is_markdown_table_row(lines[index]):
                table_lines.append(lines[index].rstrip())
                index += 1

            if table_lines and not _is_markdown_table_separator(table_lines[1] if len(table_lines) > 1 else ''):
                header_cells = [cell.strip() for cell in table_lines[0].strip().strip('|').split('|')]
                separator = '| ' + ' | '.join('---' for _ in header_cells) + ' |'
                normalized.append(table_lines[0])
                normalized.append(separator)
                normalized.extend(table_lines[1:])
            else:
                normalized.extend(table_lines)
            continue

        normalized.append(line)
        index += 1

    return '\n'.join(normalized)


def _is_markdown_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith('|') and stripped.endswith('|') and stripped.count('|') >= 2


def _is_markdown_table_separator(line: str) -> bool:
    stripped = line.strip()
    if not _is_markdown_table_row(stripped):
        return False
    cells = [cell.strip() for cell in stripped.strip('|').split('|')]
    return all(re.fullmatch(r':?-{3,}:?', cell or '') for cell in cells)


def _is_wechat_metadata_noise(line: str) -> bool:
    if not line:
        return False
    if line == '原创':
        return True
    if line.startswith('原创') and '在小说阅读器中沉浸阅读' in line:
        return True
    if '在小说阅读器中沉浸阅读' in line:
        return True
    if line.startswith('原创') and len(line) < 40:
        return True
    if line.startswith('微信扫一扫'):
        return True
    if line.startswith('喜欢此内容的人还喜欢'):
        return True
    if line.startswith('继续滑动看下一个'):
        return True
    if line.startswith('作者：') and len(line) < 40:
        return True
    if line.startswith('公众号：') and len(line) < 40:
        return True
    return False


def _remove_promotional_content(markdown: str, summary: Dict[str, object]) -> str:
    lines = markdown.split('\n')
    result: List[str] = []
    in_code_block = False
    skipping_block = False
    skip_level = 0
    pending_blank = 0

    for raw_line in lines:
        stripped = raw_line.strip()

        if stripped.startswith('```'):
            if not skipping_block:
                result.append(raw_line)
            in_code_block = not in_code_block
            continue

        if in_code_block:
            if not skipping_block:
                result.append(raw_line)
            continue

        if skipping_block:
            if stripped.startswith('原文链接:'):
                skipping_block = False
                skip_level = 0
            else:
                heading_level = _heading_level(stripped)
                if heading_level and heading_level <= skip_level:
                    skipping_block = False
                    skip_level = 0
                else:
                    if stripped:
                        if _is_contact_line(stripped):
                            summary['removed_contact_lines'] = int(summary['removed_contact_lines']) + 1
                        elif not stripped.startswith('!['):
                            summary['removed_promotion_lines'] = int(summary['removed_promotion_lines']) + 1
                    pending_blank = 1 if stripped == '' else 0
                    continue

        if _is_promotion_section_heading(stripped):
            summary['removed_promotion_blocks'] = int(summary['removed_promotion_blocks']) + 1
            skipping_block = True
            skip_level = _heading_level(stripped) or 6
            pending_blank = 0
            continue

        if _is_promotional_or_contact_line(stripped):
            summary_key = 'removed_contact_lines' if _is_contact_line(stripped) else 'removed_promotion_lines'
            summary[summary_key] = int(summary[summary_key]) + 1
            pending_blank = 1 if stripped == '' else 0
            continue

        if pending_blank and result and result[-1] != '':
            result.append('')
        pending_blank = 0
        result.append(raw_line)

    while result and result[-1] == '':
        result.pop()
    return '\n'.join(result)


def _heading_level(line: str) -> int:
    match = re.match(r'^(#{1,6})\s+', line)
    return len(match.group(1)) if match else 0


def _is_contact_line(line: str) -> bool:
    contact_patterns = [
        re.compile(r'私信回复', re.IGNORECASE),
        re.compile(r'扫码.*(?:进群|交流群|社群|社区)', re.IGNORECASE),
        re.compile(r'(?:加群|进群|交流群|社群|社区)', re.IGNORECASE),
        re.compile(r'商务合作', re.IGNORECASE),
        re.compile(r'联系我', re.IGNORECASE),
        re.compile(r'微信号|微信：|VX[:：]?\s*|vx[:：]?\s*|V信', re.IGNORECASE),
    ]
    return any(pattern.search(line) for pattern in contact_patterns)


def _is_promotional_or_contact_line(line: str) -> bool:
    if not line:
        return False
    if line.startswith('原文链接:'):
        return False
    if _is_contact_line(line):
        return True
    return any(pattern.search(line) for pattern in PROMOTION_LINE_PATTERNS + AUTHOR_INTRO_PATTERNS)


def convert_article_to_markdown(
    article: ArticleData,
    output_dir: Path,
    timeout: int,
    image_downloader: Optional[MarkdownImageDownloader] = None,
) -> tuple[str, int, str, Dict[str, object]]:
    downloader = image_downloader or MarkdownImageDownloader(
        output_dir=output_dir,
        base_url=article.original_url,
        timeout=timeout,
        image_mode=(os.environ.get('WECHAT_MD_IMAGE_MODE') or DEFAULT_IMAGE_MODE).strip() or DEFAULT_IMAGE_MODE,
    )
    parser = HTMLToMarkdownParser(downloader)

    article_html = f'''
    <article>
        <h1>{html.escape(article.title)}</h1>
        <p>作者: {html.escape(article.author or '未知')}</p>
        <div>{article.content_html}</div>
        <p>原文链接: <a href="{html.escape(article.original_url)}">{html.escape(article.original_url)}</a></p>
    </article>
    '''

    parser.feed(article_html)
    image_count = getattr(downloader, 'image_index', 0)
    image_summary = downloader.get_summary() if hasattr(downloader, 'get_summary') else {}
    return parser.get_markdown(), image_count, article_html, image_summary


def build_output_paths(title: str, output_base_dir: Path) -> tuple[Path, Path, str]:
    safe_title = sanitize_filename(title)
    folder_number = get_next_folder_number(output_base_dir)
    folder_name = f'{folder_number:02d}_{safe_title}'
    output_dir = output_base_dir / folder_name
    output_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = output_dir / f'{safe_title}.md'
    return output_dir, markdown_path, folder_name


def run_article_pipeline(
    article: ArticleData,
    output_base_dir: Path,
    save_html: bool,
    timeout: int,
    source_html: str | None = None,
) -> Dict[str, object]:
    output_dir, markdown_path, folder_name = build_output_paths(article.title, output_base_dir)
    downloader = MarkdownImageDownloader(
        output_dir=output_dir,
        base_url=article.original_url,
        timeout=timeout,
        image_mode=(os.environ.get('WECHAT_MD_IMAGE_MODE') or DEFAULT_IMAGE_MODE).strip() or DEFAULT_IMAGE_MODE,
    )

    raw_markdown, image_count, clean_article_html, image_summary = convert_article_to_markdown(
        article,
        output_dir,
        timeout,
        image_downloader=downloader,
    )
    formatted_markdown, format_summary = format_markdown(raw_markdown, output_dir)
    downloader.cleanup_unused_uploads(formatted_markdown)
    image_summary = downloader.get_summary()
    markdown_path.write_text(formatted_markdown, encoding='utf-8')

    html_path = None
    if save_html:
        html_path = output_dir / 'source.html'
        html_content = source_html or clean_article_html
        html_path.write_text(html_content, encoding='utf-8')

    return {
        'title': article.title,
        'author': article.author,
        'account_name': article.account_name,
        'original_url': article.original_url,
        'output_dir': str(output_dir),
        'folder_name': folder_name,
        'markdown_file': str(markdown_path),
        'html_file': str(html_path) if html_path else None,
        'image_count': image_count,
        'image_summary': image_summary,
        'format_summary': format_summary,
        'clean_html_preview_length': len(clean_article_html),
    }


def run_pipeline(url: str, output_base_dir: Path, save_html: bool, timeout: int) -> Dict[str, object]:
    pipeline = WeChatArticlePipeline(timeout=timeout)
    if not pipeline.validate_url(url):
        raise ValueError('无效的微信文章链接，仅支持 mp.weixin.qq.com 或 weixin.qq.com')

    source_html = pipeline.fetch_html(url)
    article = pipeline.extract_article(source_html, url)
    return run_article_pipeline(
        article=article,
        output_base_dir=output_base_dir,
        save_html=save_html,
        timeout=timeout,
        source_html=pipeline.build_clean_html(article),
    )


def safe_print(*values: object, sep: str = ' ', end: str = '\n', file=None) -> None:
    stream = file if file is not None else sys.stdout
    text = sep.join(str(value) for value in values)
    try:
        print(text, end=end, file=stream)
    except UnicodeEncodeError:
        encoding = getattr(stream, 'encoding', None) or sys.getdefaultencoding()
        fallback = text.encode(encoding, errors='replace').decode(encoding, errors='replace')
        print(fallback, end=end, file=stream)


def print_summary(result: Dict[str, object]) -> None:
    safe_print('✅ WeChat 文章处理完成\n')
    safe_print(f"标题: {result['title']}")
    if result['author']:
        safe_print(f"作者: {result['author']}")
    if result['account_name']:
        safe_print(f"公众号: {result['account_name']}")
    safe_print(f"输出目录: {result['output_dir']}")
    safe_print(f"Markdown: {result['markdown_file']}")
    if result['html_file']:
        safe_print(f"HTML: {result['html_file']}")
    safe_print(f"图片数量: {result['image_count']}")
    image_summary = result.get('image_summary') or {}
    if isinstance(image_summary, dict) and image_summary:
        safe_print('\n图片处理摘要:')
        safe_print(f"- 上传到图床: {image_summary.get('uploaded_images', 0)} 张")
        safe_print(f"- GIF 保留原链接: {image_summary.get('gif_passthrough_images', 0)} 张")
        safe_print(f"- 回退原图链接: {image_summary.get('fallback_original_url_images', 0)} 张")
        safe_print(f"- 微信原链保留: {image_summary.get('wechat_hotlink_images', 0)} 张")
        safe_print(f"- 删除未引用上传图: {image_summary.get('deleted_unused_uploads', 0)} 张")
        safe_print(
            f"- 原始总大小: {_format_bytes(int(image_summary.get('original_bytes', 0)))}"
        )
        safe_print(
            f"- 压缩后总大小: {_format_bytes(int(image_summary.get('compressed_bytes', 0)))}"
        )
        safe_print(
            f"- 节省体积: {_format_bytes(int(image_summary.get('saved_bytes', 0)))} "
            f"({image_summary.get('saved_ratio', 0)}%)"
        )

    summary = result['format_summary']
    assert isinstance(summary, dict)
    missing_images = summary.get('removed_missing_images', [])
    safe_print('\n格式化摘要:')
    safe_print(f"- 移除重复标题: {summary.get('removed_duplicate_headings', 0)} 处")
    safe_print(f"- 规范标题层级: {summary.get('normalized_heading_levels', 0)} 处")
    safe_print(f"- 清理无效链接: {summary.get('fixed_invalid_links', 0)} 处")
    safe_print(f"- 移除公众号噪音行: {summary.get('removed_noise_lines', 0)} 处")
    safe_print(f"- 移除推广区块: {summary.get('removed_promotion_blocks', 0)} 处")
    safe_print(f"- 移除推广文案行: {summary.get('removed_promotion_lines', 0)} 处")
    safe_print(f"- 移除联系方式行: {summary.get('removed_contact_lines', 0)} 处")
    safe_print(f"- 调整空行: {summary.get('trimmed_blank_lines', 0)} 处")
    safe_print(f"- 删除缺失图片引用: {len(missing_images)} 处")
    if missing_images:
        safe_print('  ' + ', '.join(str(item) for item in missing_images))


def get_workspace_dir() -> Path:
    """获取工作区目录，优先使用环境变量，否则使用当前目录。"""
    workspace_env = os.environ.get('WORKSPACE_DIR') or os.environ.get('PROJECT_ROOT') or os.environ.get('CLAUDE_WORKSPACE')
    if workspace_env:
        return Path(workspace_env).resolve()
    return Path.cwd().resolve()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Fetch WeChat public articles and convert them to formatted Markdown.'
    )
    parser.add_argument('url', help='微信文章链接')
    parser.add_argument(
        '--output-dir',
        default=None,
        help='输出根目录，默认: {工作区}/articles',
    )
    parser.add_argument(
        '--workspace-dir',
        default=None,
        help='工作区目录，默认自动检测（环境变量 WORKSPACE_DIR/PROJECT_ROOT/CLAUDE_WORKSPACE 或当前目录）',
    )
    parser.add_argument(
        '--save-html',
        action='store_true',
        help='额外保存清洗后的 HTML 文件',
    )
    parser.add_argument(
        '--timeout',
        type=int,
        default=30,
        help='网络请求超时秒数，默认 30',
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # 确定工作区目录
    if args.workspace_dir:
        workspace_dir = Path(args.workspace_dir).resolve()
    else:
        workspace_dir = get_workspace_dir()

    # 确定输出目录
    if args.output_dir:
        output_base_dir = Path(args.output_dir).resolve()
    else:
        output_base_dir = workspace_dir / 'articles'

    try:
        result = run_pipeline(
            url=args.url,
            output_base_dir=output_base_dir,
            save_html=args.save_html,
            timeout=args.timeout,
        )
    except requests.RequestException as error:
        safe_print(f'抓取失败: {error}', file=sys.stderr)
        sys.exit(1)
    except Exception as error:
        safe_print(f'处理失败: {error}', file=sys.stderr)
        sys.exit(1)

    print_summary(result)


if __name__ == '__main__':
    main()
