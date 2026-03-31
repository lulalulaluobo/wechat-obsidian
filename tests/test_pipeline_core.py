import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = (
    Path(__file__).resolve().parents[1] / "app" / "core" / "pipeline.py"
)


def load_pipeline_module():
    spec = importlib.util.spec_from_file_location("wechat_article_pipeline", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class PrintSummaryEncodingTests(unittest.TestCase):
    def test_print_summary_handles_non_utf8_stdout(self):
        module = load_pipeline_module()
        result = {
            "title": "示例标题",
            "author": "示例作者",
            "account_name": "示例公众号",
            "output_dir": r"D:\obsidian\00_Inbox\01_示例标题",
            "folder_name": "01_示例标题",
            "markdown_file": r"D:\obsidian\00_Inbox\01_示例标题\示例标题.md",
            "html_file": None,
            "image_count": 2,
            "format_summary": {
                "removed_duplicate_headings": 1,
                "normalized_heading_levels": 0,
                "fixed_invalid_links": 0,
                "removed_noise_lines": 0,
                "trimmed_blank_lines": 0,
                "removed_missing_images": [],
            },
            "clean_html_preview_length": 128,
            "image_summary": {
                "original_bytes": 2048,
                "compressed_bytes": 1024,
                "saved_bytes": 1024,
                "saved_ratio": 50.0,
                "uploaded_images": 1,
                "gif_passthrough_images": 1,
                "fallback_original_url_images": 0,
                "deleted_unused_uploads": 0,
            },
        }

        buffer = io.BytesIO()
        gbk_stdout = io.TextIOWrapper(buffer, encoding="gbk")

        with redirect_stderr(io.StringIO()):
            original_stdout = module.sys.stdout
            module.sys.stdout = gbk_stdout
            try:
                module.print_summary(result)
            finally:
                module.sys.stdout = original_stdout

        gbk_stdout.flush()
        output = buffer.getvalue().decode("gbk")
        self.assertIn("WeChat", output)
        self.assertIn("标题: 示例标题", output)


class ArticleExtractionTests(unittest.TestCase):
    def test_extract_article_prefers_author_name_over_account_name(self):
        module = load_pipeline_module()
        pipeline = module.WeChatArticlePipeline()
        source_html = """
        <div id="img-content">
          <h1 id="activity-name"><span>示例标题</span></h1>
          <span role="link" id="js_author_name">剧本没写这一出啊</span>
          <span class="rich_media_meta_nickname" id="profileBt">
            <a href="javascript:void(0);" id="js_name">摸鱼大队</a>
          </span>
          <div id="js_content"><p>正文</p></div>
          <div class="rich_media_tool_area"></div>
        </div>
        """

        article = pipeline.extract_article(source_html, "https://mp.weixin.qq.com/s/example")

        self.assertEqual(article.author, "剧本没写这一出啊")
        self.assertEqual(article.account_name, "摸鱼大队")


class MarkdownStructureTests(unittest.TestCase):
    def test_parser_skips_line_number_lists_and_preserves_pre_lines(self):
        module = load_pipeline_module()

        class DummyDownloader:
            def download(self, source):
                return None

        parser = module.HTMLToMarkdownParser(DummyDownloader())
        parser.feed(
            """
            <section class="code-snippet__fix code-snippet__js">
              <ul class="code-snippet__line-index code-snippet__js">
                <li></li><li></li>
              </ul>
              <pre class="code-snippet__js" data-lang="bash">
                <code><span leaf="">first line</span></code>
                <code><span leaf="">second line</span></code>
              </pre>
            </section>
            """
        )

        markdown = parser.get_markdown()

        self.assertNotIn("\n- ", markdown)
        self.assertIn("```text\n", markdown)
        self.assertIn("first line\nsecond line", markdown)
        self.assertIn("\n```", markdown)

    def test_format_markdown_preserves_code_fence_closing_marker(self):
        module = load_pipeline_module()
        markdown = "```text\nclaude --version\n```\n"

        with tempfile.TemporaryDirectory() as temp_dir:
            formatted, _ = module.format_markdown(markdown, Path(temp_dir))

        self.assertEqual(formatted, "```text\nclaude --version\n```\n")

    def test_parser_keeps_spaces_inside_pre_and_attaches_list_and_quote_text(self):
        module = load_pipeline_module()

        class DummyDownloader:
            def download(self, source):
                return None

        parser = module.HTMLToMarkdownParser(DummyDownloader())
        parser.feed(
            """
            <blockquote><p>Quoted text</p></blockquote>
            <ol><li><p>First item</p></li></ol>
            <pre><code><span>is</span>&nbsp;<span>not</span>&nbsp;<span>in</span></code></pre>
            """
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            formatted, _ = module.format_markdown(parser.get_markdown(), Path(temp_dir))

        self.assertIn("> Quoted text", formatted)
        self.assertIn("1. First item", formatted)
        self.assertIn("is not in", formatted)

    def test_parser_preserves_inline_spacing_between_sibling_nodes(self):
        module = load_pipeline_module()

        class DummyDownloader:
            def download(self, source):
                return None

        parser = module.HTMLToMarkdownParser(DummyDownloader())
        parser.feed("<blockquote><p><b>Setup notes:</b>&nbsp;<span>Native installation</span></p></blockquote>")

        with tempfile.TemporaryDirectory() as temp_dir:
            formatted, _ = module.format_markdown(parser.get_markdown(), Path(temp_dir))

        self.assertIn("> **Setup notes:** Native installation", formatted)

    def test_format_markdown_inserts_missing_space_after_bold_prefix(self):
        module = load_pipeline_module()
        markdown = "> **Setup notes:**Native installation exists\n"

        with tempfile.TemporaryDirectory() as temp_dir:
            formatted, _ = module.format_markdown(markdown, Path(temp_dir))

        self.assertEqual(formatted, "> **Setup notes:** Native installation exists\n")

    def test_convert_article_to_markdown_keeps_author_but_not_account_name(self):
        module = load_pipeline_module()
        article = module.ArticleData(
            title="示例标题",
            author="示例作者",
            account_name="示例公众号",
            content_html="<p>正文</p>",
            original_url="https://mp.weixin.qq.com/s/example",
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            markdown, _, _, _ = module.convert_article_to_markdown(article, Path(temp_dir), timeout=30)

        self.assertIn("作者: 示例作者", markdown)
        self.assertNotIn("公众号:", markdown)

    def test_format_markdown_removes_promotion_blocks_and_contact_lines(self):
        module = load_pipeline_module()
        markdown = """# 标题

作者: 示例作者

这里是正文结论。

大家好！我是瓜哥。前互联网技术副总裁，现在带队死磕 AI 编程。

如果觉得不错，点个免费的关注吧，瓜哥只分享AI编程实战干货。

## 🔥 福利领取

送你一份价值 399 元的资料包。私信回复「工具包」，直接免费领！

![image](image_01.png)

## 📚 AI编程实战小课

课程推广文案。

## 🚀 加入 AI 探索者社区

扫码进核心交流群。

## 📚 阅读更多

[更多文章](https://example.com)

原文链接: [https://mp.weixin.qq.com/s/example](https://mp.weixin.qq.com/s/example)
"""

        with tempfile.TemporaryDirectory() as temp_dir:
            image_path = Path(temp_dir) / "image_01.png"
            image_path.write_bytes(b"png")
            formatted, summary = module.format_markdown(markdown, Path(temp_dir))

        self.assertIn("这里是正文结论。", formatted)
        self.assertNotIn("大家好！我是瓜哥", formatted)
        self.assertNotIn("点个免费的关注", formatted)
        self.assertNotIn("福利领取", formatted)
        self.assertNotIn("AI编程实战小课", formatted)
        self.assertNotIn("加入 AI 探索者社区", formatted)
        self.assertNotIn("扫码进核心交流群", formatted)
        self.assertNotIn("阅读更多", formatted)
        self.assertIn("原文链接:", formatted)
        self.assertEqual(summary.get("removed_promotion_blocks"), 4)
        self.assertGreaterEqual(summary.get("removed_promotion_lines", 0), 2)
        self.assertGreaterEqual(summary.get("removed_contact_lines", 0), 1)

    def test_format_markdown_removes_read_more_heading_with_inline_links(self):
        module = load_pipeline_module()
        markdown = """# 标题

正文保留。

## 📚 阅读更多[

[更多文章](https://example.com)

原文链接: [https://mp.weixin.qq.com/s/example](https://mp.weixin.qq.com/s/example)
"""

        with tempfile.TemporaryDirectory() as temp_dir:
            formatted, summary = module.format_markdown(markdown, Path(temp_dir))

        self.assertIn("正文保留。", formatted)
        self.assertNotIn("阅读更多", formatted)
        self.assertIn("原文链接:", formatted)
        self.assertEqual(summary.get("removed_promotion_blocks"), 1)

    def test_format_markdown_handles_long_promotion_heading_without_hanging(self):
        module = load_pipeline_module()
        long_heading = "## " + ("A" * 20000) + "加入读书社区"
        markdown = f"""# 标题

正文保留。

{long_heading}

推广文案。

原文链接: [https://mp.weixin.qq.com/s/example](https://mp.weixin.qq.com/s/example)
"""

        with tempfile.TemporaryDirectory() as temp_dir:
            formatted, summary = module.format_markdown(markdown, Path(temp_dir))

        self.assertIn("正文保留。", formatted)
        self.assertNotIn("加入读书社区", formatted)
        self.assertEqual(summary.get("removed_promotion_blocks"), 1)

    def test_format_markdown_converts_single_backtick_blocks_without_swallowing_following_prose(self):
        module = load_pipeline_module()
        markdown = """实际效果：我在 Claude.ai 里说“创建一个每天采集 AI 资讯的工作流”，Claude 自动执行了这条链条：`
get_sdk_reference -> search_nodes -> get_node_types -> validate_workflow -> create_workflow_from_code`

不到 2 分钟，一个 13 节点的完整工作流就出现在我的 n8n 实例上了。

---

PART.07

连接 ChatGPT

开发工具类客户端用 Access Token 方式，在 config 里加一段：`
{
"mcpServers": {
"n8n": {
"command": "npx",
"args": [
"-y", "supergateway"
]
}
}
}`"""

        with tempfile.TemporaryDirectory() as temp_dir:
            formatted, _ = module.format_markdown(markdown, Path(temp_dir))

        self.assertIn("```text\nget_sdk_reference -> search_nodes -> get_node_types -> validate_workflow -> create_workflow_from_code\n```", formatted)
        self.assertIn("```\n\n不到 2 分钟，一个 13 节点的完整工作流就出现在我的 n8n 实例上了。", formatted)
        self.assertIn("\n\nPART.07\n", formatted)
        self.assertIn("```json\n{\n\"mcpServers\": {", formatted)
        self.assertNotIn("```text\n不到 2 分钟", formatted)

    def test_format_markdown_implicitly_closes_unterminated_single_backtick_block_before_prose(self):
        module = load_pipeline_module()
        markdown = """实际效果：我在 Claude.ai 里说“创建一个每天采集 AI 资讯的工作流”，Claude 自动执行了这条链条：`
get_sdk_reference -> search_nodes -> get_node_types -> validate_workflow -> create_workflow_from_code

不到 2 分钟，一个 13 节点的完整工作流就出现在我的 n8n 实例上了。

开发工具类客户端用 Access Token 方式，在 config 里加一段：`
{
"mcpServers": {
"n8n": {
"command": "npx"
}
}
}`"""

        with tempfile.TemporaryDirectory() as temp_dir:
            formatted, _ = module.format_markdown(markdown, Path(temp_dir))

        self.assertIn("```text\nget_sdk_reference -> search_nodes -> get_node_types -> validate_workflow -> create_workflow_from_code\n```", formatted)
        self.assertIn("```\n\n不到 2 分钟，一个 13 节点的完整工作流就出现在我的 n8n 实例上了。", formatted)
        self.assertIn("```json\n{\n\"mcpServers\": {", formatted)
        self.assertNotIn("```text\n不到 2 分钟", formatted)


class ImageUploadPipelineTests(unittest.TestCase):
    def test_load_s3_upload_config_from_environment(self):
        module = load_pipeline_module()

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch.dict(
                os.environ,
                {
                    "WECHAT_MD_IMAGE_MODE": "s3_hotlink",
                    "WECHAT_MD_IMAGE_STORAGE_ENDPOINT": "https://s3.example.com",
                    "WECHAT_MD_IMAGE_STORAGE_REGION": "auto",
                    "WECHAT_MD_IMAGE_STORAGE_BUCKET": "bucket-a",
                    "WECHAT_MD_IMAGE_STORAGE_ACCESS_KEY_ID": "key-1",
                    "WECHAT_MD_IMAGE_STORAGE_SECRET_ACCESS_KEY": "secret-1",
                    "WECHAT_MD_IMAGE_STORAGE_PATH_TEMPLATE": "wechat/{year}/{filename}",
                    "WECHAT_MD_IMAGE_STORAGE_PUBLIC_BASE_URL": "https://img.example.com",
                },
                clear=False,
            ):
                config = module.load_s3_upload_config()

        self.assertEqual(config.bucket_name, "bucket-a")
        self.assertEqual(config.public_base_url, "https://img.example.com")
        self.assertEqual(config.region, "auto")

    def test_markdown_image_downloader_keeps_wechat_source_urls_in_wechat_mode(self):
        module = load_pipeline_module()

        class NeverCalledSession:
            def get(self, url, headers=None, timeout=None):
                raise AssertionError("wechat_hotlink should not download images")

        downloader = module.MarkdownImageDownloader(
            output_dir=Path(tempfile.gettempdir()),
            base_url=None,
            timeout=30,
            image_mode="wechat_hotlink",
            http_session=NeverCalledSession(),
        )

        target = downloader.download("https://mmbiz.qpic.cn/test/640?wx_fmt=png&from=appmsg#imgIndex=0")
        summary = downloader.get_summary()

        self.assertEqual(target, "https://mmbiz.qpic.cn/test/640?wx_fmt=png&from=appmsg")
        self.assertEqual(summary.get("uploaded_images"), 0)
        self.assertEqual(summary.get("wechat_hotlink_images"), 1)

    def test_markdown_image_downloader_compresses_and_uploads_static_images(self):
        module = load_pipeline_module()
        from PIL import Image

        class FakeResponse:
            def __init__(self, content):
                self.content = content

            def raise_for_status(self):
                return None

        class FakeSession:
            def __init__(self, content):
                self.content = content

            def get(self, url, headers=None, timeout=None):
                return FakeResponse(self.content)

        class FakeUploader:
            def __init__(self):
                self.file_name = None
                self.content = None
                self.content_type = None
                self.deleted = []

            def upload(self, file_name, content, content_type):
                self.file_name = file_name
                self.content = content
                self.content_type = content_type
                return f"https://img.example.com/{file_name}"

            def delete(self, remote_url):
                self.deleted.append(remote_url)

        source_buffer = io.BytesIO()
        Image.new("RGB", (2400, 1200), color=(12, 120, 200)).save(source_buffer, format="PNG")
        fake_uploader = FakeUploader()

        with tempfile.TemporaryDirectory() as temp_dir:
            downloader = module.MarkdownImageDownloader(
                output_dir=Path(temp_dir),
                base_url=None,
                timeout=30,
                image_mode="s3_hotlink",
                uploader=fake_uploader,
                http_session=FakeSession(source_buffer.getvalue()),
            )
            target = downloader.download("https://mmbiz.qpic.cn/test/640?wx_fmt=png&from=appmsg")
            summary = downloader.get_summary()

            self.assertEqual(target, f"https://img.example.com/{fake_uploader.file_name}")
            self.assertEqual(fake_uploader.content_type, "image/webp")
            self.assertEqual(list(Path(temp_dir).iterdir()), [])
            self.assertEqual(summary.get("uploaded_images"), 1)
            self.assertEqual(summary.get("gif_passthrough_images"), 0)
            self.assertEqual(summary.get("fallback_original_url_images"), 0)
            self.assertGreater(int(summary.get("original_bytes", 0)), int(summary.get("compressed_bytes", 0)))

            with Image.open(io.BytesIO(fake_uploader.content)) as image:
                self.assertEqual(image.format, "WEBP")
                self.assertLessEqual(max(image.size), 1600)

    def test_markdown_image_downloader_keeps_gif_source_url(self):
        module = load_pipeline_module()

        class NeverCalledUploader:
            def upload(self, file_name, content, content_type):
                raise AssertionError("gif should not upload")

            def delete(self, remote_url):
                raise AssertionError("gif should not delete")

        downloader = module.MarkdownImageDownloader(
            output_dir=Path(tempfile.gettempdir()),
            base_url=None,
            timeout=30,
            image_mode="s3_hotlink",
            uploader=NeverCalledUploader(),
        )
        source = "https://mmbiz.qpic.cn/test/640?wx_fmt=gif&from=appmsg#imgIndex=0"

        target = downloader.download(source)
        summary = downloader.get_summary()

        self.assertEqual(target, "https://mmbiz.qpic.cn/test/640?wx_fmt=gif&from=appmsg")
        self.assertEqual(summary.get("gif_passthrough_images"), 1)
        self.assertEqual(summary.get("uploaded_images"), 0)

    def test_markdown_image_downloader_falls_back_to_original_url_on_upload_error(self):
        module = load_pipeline_module()
        from PIL import Image

        class FakeResponse:
            def __init__(self, content):
                self.content = content

            def raise_for_status(self):
                return None

        class FakeSession:
            def __init__(self, content):
                self.content = content

            def get(self, url, headers=None, timeout=None):
                return FakeResponse(self.content)

        class FailingUploader:
            def upload(self, file_name, content, content_type):
                raise RuntimeError("upload failed")

            def delete(self, remote_url):
                raise AssertionError("failed upload should not delete")

        source_buffer = io.BytesIO()
        Image.new("RGB", (1200, 800), color=(220, 220, 220)).save(source_buffer, format="JPEG")

        downloader = module.MarkdownImageDownloader(
            output_dir=Path(tempfile.gettempdir()),
            base_url=None,
            timeout=30,
            image_mode="s3_hotlink",
            uploader=FailingUploader(),
            http_session=FakeSession(source_buffer.getvalue()),
        )
        source = "https://mmbiz.qpic.cn/test/640?wx_fmt=jpeg&from=appmsg#imgIndex=0"

        target = downloader.download(source)
        summary = downloader.get_summary()

        self.assertEqual(target, "https://mmbiz.qpic.cn/test/640?wx_fmt=jpeg&from=appmsg")
        self.assertEqual(summary.get("uploaded_images"), 0)
        self.assertEqual(summary.get("fallback_original_url_images"), 1)

    def test_cleanup_unused_uploads_deletes_unreferenced_r2_images(self):
        module = load_pipeline_module()
        from PIL import Image

        class FakeResponse:
            def __init__(self, content):
                self.content = content

            def raise_for_status(self):
                return None

        class FakeSession:
            def __init__(self, content):
                self.content = content

            def get(self, url, headers=None, timeout=None):
                return FakeResponse(self.content)

        class FakeUploader:
            def __init__(self):
                self.deleted = []

            def upload(self, file_name, content, content_type):
                return f"https://img.example.com/{file_name}"

            def delete(self, remote_url):
                self.deleted.append(remote_url)

        source_buffer = io.BytesIO()
        Image.new("RGB", (1200, 800), color=(40, 40, 40)).save(source_buffer, format="PNG")
        fake_uploader = FakeUploader()

        downloader = module.MarkdownImageDownloader(
            output_dir=Path(tempfile.gettempdir()),
            base_url=None,
            timeout=30,
            image_mode="s3_hotlink",
            uploader=fake_uploader,
            http_session=FakeSession(source_buffer.getvalue()),
        )
        target = downloader.download("https://mmbiz.qpic.cn/test/640?wx_fmt=png&from=appmsg")
        downloader.cleanup_unused_uploads("# title\n\n正文保留，没有图片。\n")
        summary = downloader.get_summary()

        self.assertEqual(fake_uploader.deleted, [target])
        self.assertEqual(summary.get("deleted_unused_uploads"), 1)

    def test_format_markdown_removes_read_more_heading_with_eye_prefix(self):
        module = load_pipeline_module()
        markdown = """# 标题

正文保留。

## 👁 阅读更多[

[更多文章](https://example.com)

原文链接: [https://mp.weixin.qq.com/s/example](https://mp.weixin.qq.com/s/example)
"""

        with tempfile.TemporaryDirectory() as temp_dir:
            formatted, summary = module.format_markdown(markdown, Path(temp_dir))

        self.assertIn("正文保留。", formatted)
        self.assertNotIn("阅读更多", formatted)
        self.assertIn("原文链接:", formatted)
        self.assertEqual(summary.get("removed_promotion_blocks"), 1)

    def test_parser_outputs_markdown_table_with_header_separator(self):
        module = load_pipeline_module()

        class DummyDownloader:
            def download(self, source):
                return None

        parser = module.HTMLToMarkdownParser(DummyDownloader())
        parser.feed(
            """
            <table>
              <tr><th>工具</th><th>用途</th></tr>
              <tr><td>search_workflows</td><td>按名称/描述搜索工作流</td></tr>
            </table>
            """
        )

        with tempfile.TemporaryDirectory() as temp_dir:
            formatted, _ = module.format_markdown(parser.get_markdown(), Path(temp_dir))

        self.assertIn("| 工具 | 用途 |", formatted)
        self.assertIn("| --- | --- |", formatted)
        self.assertIn("| search_workflows | 按名称/描述搜索工作流 |", formatted)

    def test_format_markdown_converts_single_backtick_multiline_block_to_fenced_code(self):
        module = load_pipeline_module()
        markdown = """`

{
  "mcpServers": {
    "n8n": {
      "command": "npx"
    }
  }
}

`
"""

        with tempfile.TemporaryDirectory() as temp_dir:
            formatted, _ = module.format_markdown(markdown, Path(temp_dir))

        self.assertIn("```json", formatted)
        self.assertIn('"mcpServers"', formatted)
        self.assertIn("\n```", formatted)
        self.assertNotIn("\n`\n", formatted)

    def test_format_markdown_converts_inline_opened_multiline_backtick_block_to_fenced_code(self):
        module = load_pipeline_module()
        markdown = """开发工具类客户端用 Access Token 方式，在 config 里加一段：`

{
"mcpServers": {
"n8n": {
"command": "npx"
}
}
}`
"""

        with tempfile.TemporaryDirectory() as temp_dir:
            formatted, _ = module.format_markdown(markdown, Path(temp_dir))

        self.assertIn("开发工具类客户端用 Access Token 方式，在 config 里加一段：", formatted)
        self.assertIn("```json", formatted)
        self.assertIn('"mcpServers"', formatted)
        self.assertNotIn("加一段：`", formatted)
        self.assertNotIn("}`", formatted)

    def test_format_markdown_converts_plain_text_multiline_backtick_span_to_fenced_text(self):
        module = load_pipeline_module()
        markdown = """这是普通说明：`

这是一段普通说明文字
`
"""

        with tempfile.TemporaryDirectory() as temp_dir:
            formatted, _ = module.format_markdown(markdown, Path(temp_dir))

        self.assertIn("这是普通说明：", formatted)
        self.assertIn("```text", formatted)
        self.assertIn("这是一段普通说明文字", formatted)


if __name__ == "__main__":
    unittest.main()
