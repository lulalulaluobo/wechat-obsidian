import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.search.sogou_weixin import parse_sogou_weixin_results, search_sogou_weixin  # noqa: E402


def _mock_session(response=None, side_effect=None):
    session = Mock()
    if side_effect is not None:
        session.get.side_effect = side_effect
    else:
        session.get.return_value = response
    return session


class SearchProviderTests(unittest.TestCase):
    def test_sogou_weixin_parses_public_html(self):
        html = """
        <html><body>
          <div class="news-box">
            <ul class="news-list">
              <li>
                <div class="txt-box">
                  <h3><a target="_blank" href="https://mp.weixin.qq.com/s/abc">AI <em>编程</em>工作流实践</a></h3>
                  <p class="txt-info">摘要 <em>片段</em></p>
                  <div class="s-p">
                    <a class="account">某公众号</a>
                    <span class="s2">2026-05-01</span>
                  </div>
                </div>
              </li>
            </ul>
          </div>
        </body></html>
        """
        response = Mock(status_code=200, text=html)
        response.raise_for_status.return_value = None

        with patch("app.search.sogou_weixin.requests.Session", return_value=_mock_session(response=response)):
            results = search_sogou_weixin("AI 编程工作流", limit=10)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["title"], "AI 编程工作流实践")
        self.assertEqual(results[0]["url"], "https://mp.weixin.qq.com/s/abc")
        self.assertEqual(results[0]["source_name"], "某公众号")
        self.assertEqual(results[0]["published_at"], "2026-05-01")
        self.assertEqual(results[0]["snippet"], "摘要 片段")
        self.assertEqual(results[0]["provider"], "sogou_weixin")

    def test_sogou_weixin_empty_html_returns_empty_results(self):
        response = Mock(status_code=200, text="<html><body></body></html>")
        response.raise_for_status.return_value = None

        with patch("app.search.sogou_weixin.requests.Session", return_value=_mock_session(response=response)):
            results = search_sogou_weixin("无结果", limit=10)

        self.assertEqual(results, [])

    def test_sogou_weixin_respects_limits_below_one_page(self):
        html = "<ul class=\"news-list\">" + "".join(
            f'<li><h3><a href="https://mp.weixin.qq.com/s/item-{index}">文章 {index}</a></h3></li>'
            for index in range(8)
        ) + "</ul>"
        response = Mock(status_code=200, text=html)
        response.raise_for_status.return_value = None

        with patch("app.search.sogou_weixin.requests.Session", return_value=_mock_session(response=response)):
            results = search_sogou_weixin("AI", limit=5)

        self.assertEqual(len(results), 5)
        self.assertEqual(results[-1]["url"], "https://mp.weixin.qq.com/s/item-4")

    def test_sogou_weixin_fetches_multiple_pages_for_larger_limits(self):
        page_one = Mock(status_code=200, text="""
        <ul class="news-list">
          <li><h3><a href="https://mp.weixin.qq.com/s/page-one">第一页</a></h3></li>
        </ul>
        """)
        page_two = Mock(status_code=200, text="""
        <ul class="news-list">
          <li><h3><a href="https://mp.weixin.qq.com/s/page-two">第二页</a></h3></li>
        </ul>
        """)
        page_one.raise_for_status.return_value = None
        page_two.raise_for_status.return_value = None

        session = _mock_session(side_effect=[page_one, page_two])
        with patch("app.search.sogou_weixin.requests.Session", return_value=session):
            results = search_sogou_weixin("AI", limit=11)

        self.assertEqual([item["url"] for item in results], [
            "https://mp.weixin.qq.com/s/page-one",
            "https://mp.weixin.qq.com/s/page-two",
        ])
        self.assertEqual(session.get.call_args_list[0].kwargs["params"]["page"], 1)
        self.assertEqual(session.get.call_args_list[1].kwargs["params"]["page"], 2)

    def test_sogou_weixin_request_error_is_clear(self):
        with patch("app.search.sogou_weixin.requests.Session", return_value=_mock_session(side_effect=RuntimeError("network down"))):
            with self.assertRaisesRegex(RuntimeError, "搜狗微信搜索失败"):
                search_sogou_weixin("AI", limit=10)

    def test_sogou_weixin_resolves_relative_link_results(self):
        html = """
        <ul class="news-list">
          <li>
            <div class="txt-box">
              <h3><a href="/link?url=encoded&type=2&query=hermes&token=abc">Hermes Agent</a></h3>
              <p class="txt-info">摘要</p>
              <div class="s-p"><a class="account">某公众号</a><span class="s2">2026-05-01</span></div>
            </div>
          </li>
        </ul>
        """

        results = parse_sogou_weixin_results(
            html,
            limit=10,
            link_resolver=lambda href: "https://mp.weixin.qq.com/s/hermes-real",
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["url"], "https://mp.weixin.qq.com/s/hermes-real")


if __name__ == "__main__":
    unittest.main()
