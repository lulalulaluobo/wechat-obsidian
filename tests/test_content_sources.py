import unittest
from unittest.mock import Mock

from app.content_sources import (
    detect_source_type,
    extract_candidate_urls,
    fetch_article_from_url,
)


class ContentSourcesTests(unittest.TestCase):
    def test_detect_source_type_recognizes_supported_sources(self):
        self.assertEqual(detect_source_type("https://mp.weixin.qq.com/s/example"), "wechat")
        self.assertEqual(detect_source_type("https://example.com/post"), "web")

    def test_detect_source_type_rejects_zhihu_links(self):
        with self.assertRaisesRegex(ValueError, "知乎链接暂不支持"):
            detect_source_type("https://zhuanlan.zhihu.com/p/123456")
        with self.assertRaisesRegex(ValueError, "知乎链接暂不支持"):
            detect_source_type("https://www.zhihu.com/question/1/answer/2")

    def test_extract_candidate_urls_finds_all_supported_links(self):
        text = """
        微信 https://mp.weixin.qq.com/s/example
        知乎 https://zhuanlan.zhihu.com/p/123456
        网页 https://example.com/post?a=1
        """

        links = extract_candidate_urls(text)

        self.assertEqual(
            links,
            [
                "https://mp.weixin.qq.com/s/example",
                "https://zhuanlan.zhihu.com/p/123456",
                "https://example.com/post?a=1",
            ],
        )

    def test_fetch_article_from_url_extracts_generic_web_content(self):
        session = Mock()
        response = Mock()
        response.text = """
        <html>
          <head>
            <title>普通网页标题</title>
            <meta name="author" content="网页作者">
          </head>
          <body>
            <article>
              <h1>普通网页标题</h1>
              <p>网页正文。</p>
            </article>
          </body>
        </html>
        """
        response.raise_for_status.return_value = None
        response.encoding = "utf-8"
        session.get.return_value = response

        source_type, article, _ = fetch_article_from_url(
            "https://example.com/post",
            timeout=30,
            http_session=session,
        )

        self.assertEqual(source_type, "web")
        self.assertEqual(article.title, "普通网页标题")
        self.assertEqual(article.author, "网页作者")
        self.assertIn("网页正文", article.content_html)

    def test_fetch_article_from_url_rejects_empty_generic_web_page(self):
        session = Mock()
        response = Mock()
        response.text = """
        <!DOCTYPE html><html>
        <head>
          <meta charset="UTF-8">
          <script src="/probe.js"></script>
        </head>
        <body></body>
        </html>
        """
        response.raise_for_status.return_value = None
        response.encoding = "utf-8"
        session.get.return_value = response

        with self.assertRaisesRegex(RuntimeError, "网页正文提取失败"):
            fetch_article_from_url(
                "https://post.smzdm.com/talk/p/aoml0d96/",
                timeout=30,
                http_session=session,
            )

    def test_fetch_article_from_url_rejects_zhihu_link(self):
        with self.assertRaisesRegex(ValueError, "知乎链接暂不支持"):
            fetch_article_from_url(
                "https://zhuanlan.zhihu.com/p/123456",
                timeout=30,
                http_session=Mock(),
            )
