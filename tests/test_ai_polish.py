import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.ai_polish import (
    apply_ai_polish_to_markdown,
    build_prompt_from_variable_prompts,
    extract_prompt_variables_from_templates,
    render_template,
)  # noqa: E402
from app.services import execute_single_conversion  # noqa: E402


class AIPolishTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        runtime_path = Path(self.temp_dir.name) / "runtime-config.json"
        self.env_patcher = patch.dict(
            os.environ,
            {
                "WECHAT_MD_RUNTIME_CONFIG_PATH": str(runtime_path),
                "WECHAT_MD_APP_MASTER_KEY": "test-master-key",
                "WECHAT_MD_ADMIN_USERNAME": "admin",
                "WECHAT_MD_ADMIN_PASSWORD": "admin",
            },
            clear=False,
        )
        self.env_patcher.start()

    def tearDown(self) -> None:
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    def test_render_template_replaces_known_variables_and_blanks_unknowns(self):
        rendered = render_template(
            "title: {{title}}\nsummary: {{summary}}\nmissing: {{missing}}",
            {"title": "示例标题", "summary": "一句话"},
        )

        self.assertIn("title: 示例标题", rendered)
        self.assertIn("summary: 一句话", rendered)
        self.assertIn("missing: ", rendered)
        self.assertNotIn("{{missing}}", rendered)

    def test_apply_ai_polish_to_markdown_builds_frontmatter_and_template_blocks(self):
        markdown_path = Path(self.temp_dir.name) / "article.md"
        markdown_path.write_text("# 原文标题\n\n正文内容", encoding="utf-8")
        metadata = {"title": "示例标题", "author": "作者", "url": "https://mp.weixin.qq.com/s/example"}

        with patch(
            "app.ai_polish.request_interpreter_variables",
            return_value={
                "summary": "一句话总结",
                "tags": ["AI", "微信"],
                "my_understand": "这是我的理解",
                "body_polish": "> [!tip]\n> 额外补充",
            },
        ):
            ai_result = apply_ai_polish_to_markdown(
                markdown_path=markdown_path,
                metadata=metadata,
                ai_base_url="https://api.example.com/v1",
                ai_api_key="ai-key",
                ai_model="gpt-5.4-mini",
                interpreter_prompt="请总结 {{title}}",
                frontmatter_template="---\ntitle: {{title}}\nsummary: {{summary}}\ntags: {{tags}}\n---",
                body_template="> [!summary]\n> {{summary}}\n\n> [!tip]\n> {{my_understand}}\n\n{{body_polish}}",
                allow_body_polish=True,
            )

        text = markdown_path.read_text(encoding="utf-8")
        self.assertEqual(ai_result["status"], "success")
        self.assertIn("---", text)
        self.assertIn("summary: 一句话总结", text)
        self.assertIn('tags: ["AI", "微信"]', text)
        self.assertIn("> [!summary]", text)
        self.assertIn("这是我的理解", text)
        self.assertIn("# 原文标题", text)

    def test_execute_single_conversion_degrades_when_ai_polish_fails(self):
        markdown_path = Path(self.temp_dir.name) / "article.md"
        markdown_path.write_text("# 标题\n\n正文", encoding="utf-8")
        with patch("app.services.run_pipeline", return_value={"title": "标题", "author": "作者", "original_url": "https://mp.weixin.qq.com/s/example", "markdown_file": str(markdown_path), "folder_name": "01_标题", "image_mode": "wechat_hotlink"}):
            with patch("app.services.sync_result_to_output", return_value={"status": "success", "target": "local", "markdown_file": str(markdown_path)}):
                with patch("app.services.apply_ai_polish_to_result", side_effect=RuntimeError("ai failed")):
                    payload = execute_single_conversion(
                        url="https://mp.weixin.qq.com/s/example",
                        timeout=30,
                        save_html=False,
                        output_target="local",
                        ai_enabled=True,
                    )

        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["ai_polish"]["status"], "failed")
        self.assertEqual(payload["ai_polish"]["enabled"], True)
        self.assertIn("ai failed", payload["ai_polish"]["message"])

    def test_execute_single_conversion_raises_without_sync_when_ai_is_required_for_bot(self):
        markdown_path = Path(self.temp_dir.name) / "article-required.md"
        markdown_path.write_text("# 标题\n\n正文", encoding="utf-8")
        with patch("app.services.run_pipeline", return_value={"title": "标题", "author": "作者", "original_url": "https://mp.weixin.qq.com/s/example", "markdown_file": str(markdown_path), "folder_name": "01_标题", "image_mode": "wechat_hotlink"}):
            with patch("app.services.sync_result_to_output") as mocked_sync:
                with patch(
                    "app.services.apply_ai_polish_to_result",
                    return_value={
                        "enabled": True,
                        "status": "failed",
                        "template_applied": False,
                        "content_polished": False,
                        "message": "模板未成功应用",
                    },
                ):
                    with self.assertRaisesRegex(RuntimeError, "AI 润色失败"):
                        execute_single_conversion(
                            url="https://mp.weixin.qq.com/s/example",
                            timeout=30,
                            save_html=False,
                            output_target="local",
                            ai_enabled=True,
                            require_ai_success=True,
                        )

        mocked_sync.assert_not_called()

    def test_apply_ai_polish_does_not_duplicate_content_when_body_template_contains_content(self):
        markdown_path = Path(self.temp_dir.name) / "clipper.md"
        markdown_path.write_text("# 原文标题\n\n正文内容", encoding="utf-8")
        metadata = {"title": "示例标题", "author": "作者", "url": "https://mp.weixin.qq.com/s/example"}

        with patch(
            "app.ai_polish.request_interpreter_variables",
            return_value={"summary": "一句话总结"},
        ):
            apply_ai_polish_to_markdown(
                markdown_path=markdown_path,
                metadata=metadata,
                ai_base_url="https://api.example.com/v1",
                ai_api_key="ai-key",
                ai_model="gpt-5.4-mini",
                interpreter_prompt="请总结 {{title}}",
                frontmatter_template="---\ntitle: {{title}}\n---",
                body_template="前置块\n\n{{content}}\n\n后置块",
                context_template="{{content}}",
                allow_body_polish=False,
            )

        text = markdown_path.read_text(encoding="utf-8")
        self.assertEqual(text.count("# 原文标题"), 1)
        self.assertIn("前置块", text)
        self.assertIn("后置块", text)

    def test_apply_ai_polish_uses_content_polished_as_final_body_when_enabled(self):
        markdown_path = Path(self.temp_dir.name) / "polished.md"
        markdown_path.write_text("# 原文标题\n\n原始正文内容", encoding="utf-8")
        metadata = {"title": "示例标题", "author": "作者", "url": "https://mp.weixin.qq.com/s/example"}

        with patch(
            "app.ai_polish.request_interpreter_variables",
            return_value={
                "summary": "一句话总结",
                "tags": ["AI", "微信"],
            },
        ), patch(
            "app.ai_polish.request_polished_content",
            return_value="## 润色后正文\n\n- 更适合 Obsidian 阅读",
        ):
            apply_ai_polish_to_markdown(
                markdown_path=markdown_path,
                metadata=metadata,
                ai_base_url="https://api.example.com/v1",
                ai_api_key="ai-key",
                ai_model="gpt-5.4-mini",
                interpreter_prompt="请总结 {{title}}",
                frontmatter_template="---\ntitle: {{title}}\nsummary: {{summary}}\ntags: {{tags}}\n---",
                body_template="> [!summary]\n> {{summary}}",
                context_template="{{content}}",
                allow_body_polish=False,
                enable_content_polish=True,
                content_polish_prompt="请把正文整理为更适合 Obsidian 阅读的 Markdown",
            )

        text = markdown_path.read_text(encoding="utf-8")
        self.assertIn("## 润色后正文", text)
        self.assertIn("更适合 Obsidian 阅读", text)
        self.assertNotIn("原始正文内容", text)

    def test_apply_ai_polish_renders_content_placeholder_with_polished_body_when_enabled(self):
        markdown_path = Path(self.temp_dir.name) / "clipper-polished.md"
        markdown_path.write_text("# 原文标题\n\n原始正文内容", encoding="utf-8")
        metadata = {"title": "示例标题", "author": "作者", "url": "https://mp.weixin.qq.com/s/example"}

        with patch(
            "app.ai_polish.request_interpreter_variables",
            return_value={
                "summary": "一句话总结",
            },
        ), patch(
            "app.ai_polish.request_polished_content",
            return_value="## 润色后正文\n\n- 更适合 Obsidian 阅读",
        ):
            apply_ai_polish_to_markdown(
                markdown_path=markdown_path,
                metadata=metadata,
                ai_base_url="https://api.example.com/v1",
                ai_api_key="ai-key",
                ai_model="gpt-5.4-mini",
                interpreter_prompt='{"summary":"一句话总结","content_polished":"请把正文整理为更适合 Obsidian 阅读的 Markdown"}',
                frontmatter_template="---\ntitle: {{title}}\nsummary: {{summary}}\n---",
                body_template="前置块\n\n{{content}}\n\n后置块",
                context_template="{{content}}",
                allow_body_polish=False,
                enable_content_polish=True,
                content_polish_prompt="请把正文整理为更适合 Obsidian 阅读的 Markdown",
            )

        text = markdown_path.read_text(encoding="utf-8")
        self.assertIn("前置块", text)
        self.assertIn("## 润色后正文", text)
        self.assertNotIn("原始正文内容", text)
        self.assertIn("后置块", text)

    def test_apply_ai_polish_keeps_frontmatter_when_content_polish_request_fails(self):
        markdown_path = Path(self.temp_dir.name) / "degraded-polish.md"
        markdown_path.write_text("# 原文标题\n\n原始正文内容", encoding="utf-8")
        metadata = {"title": "示例标题", "author": "作者", "url": "https://mp.weixin.qq.com/s/example"}

        with patch(
            "app.ai_polish.request_interpreter_variables",
            return_value={
                "summary": "一句话总结",
                "tags": ["AI", "微信"],
            },
        ), patch(
            "app.ai_polish.request_polished_content",
            side_effect=RuntimeError("content polish failed"),
        ):
            ai_result = apply_ai_polish_to_markdown(
                markdown_path=markdown_path,
                metadata=metadata,
                ai_base_url="https://api.example.com/v1",
                ai_api_key="ai-key",
                ai_model="gpt-5.4-mini",
                interpreter_prompt='{"summary":"一句话总结","tags":"生成 5 个标签"}',
                frontmatter_template="---\ntitle: {{title}}\nsummary: {{summary}}\ntags: {{tags}}\n---",
                body_template="{{content}}",
                context_template="{{content}}",
                allow_body_polish=False,
                enable_content_polish=True,
                content_polish_prompt="请把正文整理为更适合 Obsidian 阅读的 Markdown",
            )

        text = markdown_path.read_text(encoding="utf-8")
        self.assertEqual(ai_result["status"], "success")
        self.assertTrue(ai_result["template_applied"])
        self.assertFalse(ai_result["content_polished"])
        self.assertIn("正文润色已降级", ai_result["message"])
        self.assertIn("summary: 一句话总结", text)
        self.assertIn('tags: ["AI", "微信"]', text)
        self.assertIn("原始正文内容", text)

    def test_build_prompt_from_variable_prompts_includes_context_and_keys(self):
        prompt = build_prompt_from_variable_prompts(
            variable_prompts={"summary": "一句话总结", "clipper_block_1": "提炼五个要点"},
            metadata={"title": "示例标题", "author": "作者", "url": "https://mp.weixin.qq.com/s/example", "date": "2026-03-29"},
            context="正文内容",
        )

        self.assertIn("summary", prompt)
        self.assertIn("clipper_block_1", prompt)
        self.assertIn("正文内容", prompt)

    def test_extract_prompt_variables_from_templates_supports_escaped_clipper_prompts(self):
        frontmatter, body, mapping = extract_prompt_variables_from_templates(
            frontmatter_template="---\nsummary: {{\\\"一句话总结\\\"}}\ntags: {{\\\"生成5个tag\\\"}}\n---",
            body_template="{{\\\"提炼五个要点\\\"}}\n\n{{content}}",
        )

        self.assertIn("summary: {{summary}}", frontmatter)
        self.assertIn("tags: {{tags}}", frontmatter)
        self.assertIn("{{clipper_block_1}}", body)
        self.assertEqual(mapping["summary"], "一句话总结")
        self.assertEqual(mapping["tags"], "生成5个tag")
        self.assertEqual(mapping["clipper_block_1"], "提炼五个要点")


if __name__ == "__main__":
    unittest.main()
