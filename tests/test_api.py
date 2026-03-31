import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.main import app  # noqa: E402
from app.auth import build_session_token, reset_login_rate_limit_state  # noqa: E402
from app.config import get_settings, reset_admin_credentials  # noqa: E402
from app.services import (  # noqa: E402
    extract_single_wechat_url,
    parse_links,
    process_feishu_convert_task,
    process_telegram_convert_task,
)
from app.task_history import TaskHistoryStore  # noqa: E402


class ApiTests(unittest.TestCase):
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
                "WECHAT_MD_SINGLE_CONVERSION_ISOLATION_ENABLED": "false",
            },
            clear=False,
        )
        self.env_patcher.start()
        reset_login_rate_limit_state()
        self.client = TestClient(app)
        self.runtime_path = runtime_path

    def tearDown(self) -> None:
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    def _login(self, username: str = "admin", password: str = "admin"):
        return self.client.post(
            "/api/session",
            json={"username": username, "password": password},
        )

    def test_root_requires_login(self):
        response = self.client.get("/", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")

    def test_login_page_renders_username_password_form(self):
        response = self.client.get("/login")

        self.assertEqual(response.status_code, 200)
        self.assertIn("管理后台登录", response.text)
        self.assertIn("同步服务管理台", response.text)
        self.assertIn("用户名", response.text)
        self.assertIn("密码", response.text)
        self.assertNotIn("跟随系统", response.text)
        self.assertNotIn('id="theme-mode"', response.text)

    def test_default_admin_login_success(self):
        login_response = self._login()
        config_response = self.client.get("/api/config")

        self.assertEqual(login_response.status_code, 200)
        self.assertEqual(config_response.status_code, 200)

    def test_index_page_is_fns_only_after_login(self):
        self._login()
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("工作台概览", response.text)
        self.assertIn("转换中心", response.text)
        self.assertIn("系统状态", response.text)
        self.assertIn("任务摘要", response.text)
        self.assertIn("最近结果", response.text)
        self.assertIn("粘贴", response.text)
        self.assertIn("本次启用 AI 润色", response.text)
        self.assertIn("原始 JSON", response.text)
        self.assertNotIn("输出目录", response.text)
        self.assertNotIn("输出目标", response.text)
        self.assertNotIn("写入本地目录", response.text)
        self.assertIn('document.getElementById("single-url").value = "";', response.text)
        self.assertIn('document.getElementById("batch-urls").value = "";', response.text)
        self.assertIn('fileInput.value = "";', response.text)

    def test_tasks_page_renders_after_login(self):
        self._login()
        response = self.client.get("/tasks")

        self.assertEqual(response.status_code, 200)
        self.assertIn("任务历史", response.text)
        self.assertIn("重跑选中任务", response.text)

    def test_wrong_password_is_rejected(self):
        response = self._login(password="wrong-password")

        self.assertEqual(response.status_code, 401)

    def test_login_is_rate_limited_after_repeated_failures(self):
        for _ in range(5):
            response = self._login(password="wrong-password")
            self.assertEqual(response.status_code, 401)

        blocked = self._login(password="wrong-password")

        self.assertEqual(blocked.status_code, 429)
        self.assertIn("Retry-After", blocked.headers)

    def test_logout_blocks_protected_endpoints(self):
        self._login()
        self.client.delete("/api/session")

        response = self.client.get("/api/config")

        self.assertEqual(response.status_code, 401)

    def test_convert_success(self):
        self._login()
        fake_result = {
            "status": "success",
            "output_target": "local",
            "result": {"title": "示例", "markdown_file": r"D:\obsidian\00_Inbox\01_示例\示例.md"},
            "sync": {"status": "success", "target": "local", "markdown_file": r"D:\obsidian\00_Inbox\01_示例\示例.md"},
            "local_artifacts": {"retained": False, "workdir": None},
        }
        with patch("app.api.routes.execute_single_conversion", return_value=fake_result):
            response = self.client.post("/api/convert", json={"url": "https://mp.weixin.qq.com/s/example"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        self.assertEqual(response.json()["result"]["title"], "示例")

    def test_convert_response_includes_task_metadata(self):
        self._login()
        fake_result = {
            "status": "success",
            "task_id": "task-1",
            "source_type": "web",
            "output_target": "local",
            "result": {"title": "网页示例", "markdown_file": r"D:\obsidian\00_Inbox\01_网页示例\网页示例.md"},
            "sync": {"status": "success", "target": "local", "markdown_file": r"D:\obsidian\00_Inbox\01_网页示例\网页示例.md"},
            "local_artifacts": {"retained": False, "workdir": None},
        }
        with patch("app.api.routes.execute_single_conversion", return_value=fake_result):
            response = self.client.post("/api/convert", json={"url": "https://example.com/post"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["task_id"], "task-1")
        self.assertEqual(response.json()["source_type"], "web")

    def test_convert_rejects_zhihu_link(self):
        self._login()

        response = self.client.post("/api/convert", json={"url": "https://zhuanlan.zhihu.com/p/123456"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("知乎链接暂不支持", response.json()["detail"])

    def test_convert_passes_ai_override(self):
        self._login()
        fake_result = {
            "status": "success",
            "output_target": "fns",
            "result": {"title": "示例", "markdown_file": r"D:\obsidian\00_Inbox\01_示例\示例.md"},
            "sync": {"status": "success", "target": "fns", "path": "00_Inbox/微信公众号/示例.md"},
            "local_artifacts": {"retained": False, "workdir": None},
            "ai_polish": {"enabled": True, "status": "success", "model": "gpt-5.4-mini"},
        }
        with patch("app.api.routes.execute_single_conversion", return_value=fake_result) as mocked_execute:
            response = self.client.post(
                "/api/convert",
                json={"url": "https://mp.weixin.qq.com/s/example", "ai_enabled": True},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(mocked_execute.call_args.kwargs["ai_enabled"])
        self.assertTrue(mocked_execute.call_args.kwargs["require_ai_success"])

    def test_login_cookie_uses_secure_flag_when_enabled(self):
        with patch.dict(os.environ, {"WECHAT_MD_SESSION_COOKIE_SECURE": "true"}, clear=False):
            response = self._login()

        self.assertEqual(response.status_code, 200)
        cookie_header = response.headers.get("set-cookie", "")
        self.assertIn("Secure", cookie_header)

    def test_convert_defaults_to_fns_when_configured(self):
        self._login()
        with tempfile.TemporaryDirectory() as temp_dir:
            markdown_path = Path(temp_dir) / "article.md"
            markdown_path.write_text("# 示例\n\n正文", encoding="utf-8")
            fake_result = {
                "title": "示例",
                "folder_name": "01_示例",
                "markdown_file": str(markdown_path),
            }
            fake_sync = {
                "status": "success",
                "target": "fns",
                "vault": "MainVault",
                "path": "00_Inbox/微信公众号/示例.md",
            }
            payload = {
                "fns_base_url": "https://fns.example.com",
                "fns_token": "fns-token",
                "fns_vault": "MainVault",
                "fns_target_dir": "00_Inbox/微信公众号",
            }
            self.client.put("/api/admin/settings", json=payload)
            with patch(
                "app.services.fetch_article_from_url",
                return_value=(
                    "wechat",
                    SimpleNamespace(title="示例", author="", account_name="", content_html="<p>正文</p>", original_url="https://mp.weixin.qq.com/s/example"),
                    "<html></html>",
                    {"fetch_status": "success", "content_kind": "article", "comment_id": "", "cache_hit": False, "failure_reason": ""},
                ),
            ):
                with patch("app.services.run_article_pipeline", return_value=fake_result):
                    with patch("app.services.sync_result_to_output", return_value=fake_sync) as mocked_sync:
                        response = self.client.post(
                            "/api/convert",
                            json={"url": "https://mp.weixin.qq.com/s/example"},
                        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["output_target"], "fns")
        self.assertEqual(data["sync"]["path"], "00_Inbox/微信公众号/示例.md")
        mocked_sync.assert_called_once()

    def test_convert_uses_internal_workdir_for_fns_and_cleans_up_on_success(self):
        self._login()
        self.client.put(
            "/api/admin/settings",
            json={
                "fns_base_url": "https://fns.example.com",
                "fns_token": "fns-token",
                "fns_vault": "MainVault",
                "fns_target_dir": "00_Inbox/微信公众号",
                "cleanup_temp_on_success": True,
            },
        )
        captured: dict[str, Path] = {}

        def fake_run_article_pipeline(article, output_base_dir, save_html, timeout, source_html):
            base_dir = Path(output_base_dir)
            captured["base_dir"] = base_dir
            article_dir = base_dir / "01_示例"
            article_dir.mkdir(parents=True, exist_ok=True)
            markdown_path = article_dir / "示例.md"
            markdown_path.write_text("# 示例\n\n正文", encoding="utf-8")
            return {
                "title": "示例",
                "folder_name": "01_示例",
                "markdown_file": str(markdown_path),
                "output_dir": str(article_dir),
            }

        with patch(
            "app.services.fetch_article_from_url",
            return_value=(
                "wechat",
                SimpleNamespace(title="示例", author="", account_name="", content_html="<p>正文</p>", original_url="https://mp.weixin.qq.com/s/example"),
                "<html></html>",
                {"fetch_status": "success", "content_kind": "article", "comment_id": "", "cache_hit": False, "failure_reason": ""},
            ),
        ):
            with patch("app.services.run_article_pipeline", side_effect=fake_run_article_pipeline):
                with patch(
                    "app.services.sync_result_to_output",
                    return_value={"status": "success", "target": "fns", "path": "00_Inbox/微信公众号/示例.md"},
                ):
                    response = self.client.post(
                        "/api/convert",
                        json={"url": "https://mp.weixin.qq.com/s/example"},
                    )

        self.assertEqual(response.status_code, 200)
        self.assertIn("workdir", str(captured["base_dir"]))
        self.assertNotEqual(captured["base_dir"], Path(r"D:\obsidian\00_Inbox"))
        self.assertFalse(captured["base_dir"].exists())

    def test_convert_retains_internal_workdir_when_cleanup_disabled(self):
        self._login()
        self.client.put(
            "/api/admin/settings",
            json={
                "fns_base_url": "https://fns.example.com",
                "fns_token": "fns-token",
                "fns_vault": "MainVault",
                "fns_target_dir": "00_Inbox/微信公众号",
                "cleanup_temp_on_success": False,
            },
        )
        captured: dict[str, Path] = {}

        def fake_run_article_pipeline(article, output_base_dir, save_html, timeout, source_html):
            base_dir = Path(output_base_dir)
            captured["base_dir"] = base_dir
            article_dir = base_dir / "01_示例"
            article_dir.mkdir(parents=True, exist_ok=True)
            markdown_path = article_dir / "示例.md"
            markdown_path.write_text("# 示例\n\n正文", encoding="utf-8")
            return {
                "title": "示例",
                "folder_name": "01_示例",
                "markdown_file": str(markdown_path),
                "output_dir": str(article_dir),
            }

        with patch(
            "app.services.fetch_article_from_url",
            return_value=(
                "wechat",
                SimpleNamespace(title="示例", author="", account_name="", content_html="<p>正文</p>", original_url="https://mp.weixin.qq.com/s/example"),
                "<html></html>",
                {"fetch_status": "success", "content_kind": "article", "comment_id": "", "cache_hit": False, "failure_reason": ""},
            ),
        ):
            with patch("app.services.run_article_pipeline", side_effect=fake_run_article_pipeline):
                with patch(
                    "app.services.sync_result_to_output",
                    return_value={"status": "success", "target": "fns", "path": "00_Inbox/微信公众号/示例.md"},
                ):
                    response = self.client.post(
                        "/api/convert",
                        json={"url": "https://mp.weixin.qq.com/s/example"},
                    )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(captured["base_dir"].exists())
        self.assertIn("workdir", str(captured["base_dir"]))
        shutil.rmtree(captured["base_dir"], ignore_errors=True)

    def test_sync_config_api_masks_sensitive_fields(self):
        self._login()
        response = self.client.put(
            "/api/sync/config",
            json={
                "wechat_mp_token": "mp-token",
                "wechat_mp_cookie": "ua=1; bizuin=2; pass_ticket=3",
            },
        )

        self.assertEqual(response.status_code, 200)
        config = self.client.get("/api/sync/config").json()
        self.assertTrue(config["wechat_mp_token_configured"])
        self.assertTrue(config["wechat_mp_cookie_configured"])

    def test_sync_source_api_creates_and_lists_source(self):
        self._login()
        create_response = self.client.post(
            "/api/sync/sources",
            json={"fakeid": "fakeid-1", "nickname": "示例公众号", "alias": "demo"},
        )

        self.assertEqual(create_response.status_code, 200)
        list_response = self.client.get("/api/sync/sources")
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(list_response.json()["items"][0]["account_fakeid"], "fakeid-1")

    def test_batch_from_text(self):
        self._login()
        with patch("app.api.routes.job_store.create_batch_job") as mocked:
            mocked.return_value = {
                "job_id": "job-1",
                "total": 2,
                "output_dir": r"D:\obsidian\00_Inbox",
            }
            response = self.client.post(
                "/api/batch",
                data={"urls_text": "https://mp.weixin.qq.com/s/a\nhttps://mp.weixin.qq.com/s/b"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["job_id"], "job-1")
        self.assertEqual(response.json()["deduped_count"], 2)

    def test_batch_passes_ai_override(self):
        self._login()
        with patch("app.api.routes.job_store.create_batch_job") as mocked:
            mocked.return_value = {
                "job_id": "job-ai",
                "total": 1,
                "output_dir": r"D:\obsidian\00_Inbox",
            }
            response = self.client.post(
                "/api/batch",
                data={"urls_text": "https://mp.weixin.qq.com/s/a", "ai_enabled": "true"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(mocked.call_args.kwargs["ai_enabled"])
        self.assertTrue(mocked.call_args.kwargs["require_ai_success"])

    def test_batch_from_file(self):
        self._login()
        with patch("app.api.routes.job_store.create_batch_job") as mocked:
            mocked.return_value = {
                "job_id": "job-file",
                "total": 1,
                "output_dir": r"D:\obsidian\00_Inbox",
            }
            response = self.client.post(
                "/api/batch",
                files={"file": ("links.txt", b"https://mp.weixin.qq.com/s/file-example", "text/plain")},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["job_id"], "job-file")

    def test_parse_links_accepts_query_style_wechat_links(self):
        links = parse_links(
            urls_text="这里有一条微信文章：https://mp.weixin.qq.com/s?__biz=MzA4OTU3NzQ2OA==&mid=2661544116&idx=1&sn=abcd1234"
        )

        self.assertEqual(
            links,
            ["https://mp.weixin.qq.com/s?__biz=MzA4OTU3NzQ2OA==&mid=2661544116&idx=1&sn=abcd1234"],
        )

    def test_extract_single_wechat_url_accepts_query_style_wechat_link(self):
        url, url_count = extract_single_wechat_url(
            "这里是文章 https://mp.weixin.qq.com/s?__biz=MzA4OTU3NzQ2OA==&mid=2661544116&idx=1&sn=abcd1234"
        )

        self.assertEqual(
            url,
            "https://mp.weixin.qq.com/s?__biz=MzA4OTU3NzQ2OA==&mid=2661544116&idx=1&sn=abcd1234",
        )
        self.assertEqual(url_count, 1)

    def test_parse_links_accepts_generic_urls(self):
        links = parse_links(
            urls_text="""
            https://mp.weixin.qq.com/s/example
            https://example.com/post?a=1
            """
        )

        self.assertEqual(
            links,
            [
                "https://mp.weixin.qq.com/s/example",
                "https://example.com/post?a=1",
            ],
        )

    def test_extract_single_wechat_url_accepts_generic_web_link(self):
        url, url_count = extract_single_wechat_url("请处理 https://example.com/post?a=1")

        self.assertEqual(url, "https://example.com/post?a=1")
        self.assertEqual(url_count, 1)

    def test_tasks_api_lists_history_records(self):
        self._login()
        store = TaskHistoryStore(Path(self.temp_dir.name) / "task-history.jsonl")
        first = store.create_task(
            trigger_channel="web",
            source_type="wechat",
            source_url="https://mp.weixin.qq.com/s/example",
        )
        store.update_task(first["task_id"], status="success", note_title="微信笔记")
        second = store.create_task(
            trigger_channel="telegram",
            source_type="web",
            source_url="https://example.com/error",
        )
        store.update_task(second["task_id"], status="error", error_message="boom")

        response = self.client.get("/api/tasks?source_type=web&status=error")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["task_id"], second["task_id"])
        self.assertEqual(payload["items"][0]["source_type"], "web")

    def test_single_rerun_api_accepts_existing_task(self):
        self._login()
        store = TaskHistoryStore(Path(self.temp_dir.name) / "task-history.jsonl")
        task = store.create_task(
            trigger_channel="web",
            source_type="web",
            source_url="https://example.com/post",
        )

        with patch(
            "app.api.routes.submit_rerun_task",
            return_value={"task_id": "rerun-1", "rerun_of_task_id": task["task_id"]},
        ) as mocked:
            response = self.client.post(f"/api/tasks/{task['task_id']}/rerun")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "accepted")
        self.assertEqual(response.json()["task_id"], "rerun-1")
        mocked.assert_called_once_with(task["task_id"])

    def test_batch_rerun_api_accepts_selected_tasks(self):
        self._login()

        with patch(
            "app.api.routes.submit_rerun_tasks",
            return_value={
                "accepted": 2,
                "items": [
                    {"task_id": "rerun-1", "rerun_of_task_id": "task-1"},
                    {"task_id": "rerun-2", "rerun_of_task_id": "task-2"},
                ],
            },
        ) as mocked:
            response = self.client.post("/api/tasks/rerun", json={"task_ids": ["task-1", "task-2"]})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "accepted")
        self.assertEqual(response.json()["accepted"], 2)
        mocked.assert_called_once_with(["task-1", "task-2"])

    def test_batch_uses_internal_workdir_root_for_fns(self):
        self._login()
        self.client.put(
            "/api/admin/settings",
            json={
                "fns_base_url": "https://fns.example.com",
                "fns_token": "fns-token",
                "fns_vault": "MainVault",
                "fns_target_dir": "00_Inbox/微信公众号",
            },
        )
        with patch("app.api.routes.job_store.create_batch_job") as mocked:
            mocked.return_value = {
                "job_id": "job-fns",
                "total": 1,
                "output_dir": str(Path(self.temp_dir.name) / "workdir"),
            }
            response = self.client.post(
                "/api/batch",
                data={"urls_text": "https://mp.weixin.qq.com/s/a"},
            )

        self.assertEqual(response.status_code, 200)
        output_dir = mocked.call_args.kwargs["output_dir"]
        self.assertIn("workdir", str(output_dir))
        self.assertNotEqual(Path(output_dir), Path(r"D:\obsidian\00_Inbox"))

    def test_settings_page_contains_fns_import_actions_when_logged_in(self):
        self._login()
        response = self.client.get("/settings")

        self.assertEqual(response.status_code, 200)
        text = response.text
        self.assertIn("配置总览", text)
        self.assertIn("连接诊断", text)
        self.assertIn("FNS 配置", text)
        self.assertIn("运行行为", text)
        self.assertIn("从剪贴板导入 FNS", text)
        self.assertIn("解析并填充", text)
        self.assertIn('id="fns-json-input"', text)
        self.assertIn("设置中心", text)
        self.assertIn("检测 FNS 连接", text)
        self.assertIn('id="fns-status-result"', text)
        self.assertIn("当前登录用户", text)
        self.assertIn("修改密码", text)
        self.assertIn('id="change-pw-btn" class="btn btn-danger"', text)
        self.assertIn("图片外链设置", text)
        self.assertIn("微信原链", text)
        self.assertIn("S3 图床外链", text)
        self.assertIn("Telegram Bot", text)
        self.assertIn("飞书 Bot", text)
        self.assertIn("Bot Token", text)
        self.assertIn("Webhook 对外基础地址", text)
        self.assertIn("白名单 Chat ID", text)
        self.assertIn("AI 润色", text)
        self.assertIn("Provider 管理", text)
        self.assertIn("当前使用模型", text)
        self.assertIn("连接与当前模型", text)
        self.assertIn("解释器提示词", text)
        self.assertIn("frontmatter 模板", text)
        self.assertIn("body 模板", text)
        self.assertIn("测试 AI 连通性", text)
        self.assertIn("导入 Clipper JSON 模板", text)
        self.assertIn('id="clipper-json-file"', text)
        self.assertNotIn('id="paste-clipper-btn"', text)
        self.assertNotIn('data-provider-field="enabled"', text)
        self.assertNotIn('data-model-field="enabled"', text)
        self.assertNotIn('data-model-selected="true"', text)
        self.assertIn('document.getElementById("ai-provider-list").addEventListener("click"', text)

    def test_admin_settings_masks_telegram_secret_values(self):
        self._login()
        with patch("app.api.routes.configure_telegram_webhook", return_value={"status": "success", "message": "ok", "webhook_url": "https://app.example.com/api/integrations/telegram/webhook"}):
            self.client.put(
                "/api/admin/settings",
                json={
                    "telegram_enabled": True,
                    "telegram_bot_token": "telegram-token-1",
                    "telegram_webhook_public_base_url": "https://app.example.com",
                    "telegram_webhook_secret": "telegram-secret-1",
                    "telegram_allowed_chat_ids": "123456\n789000",
                    "telegram_notify_on_complete": True,
                },
            )

        response = self.client.get("/api/admin/settings")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["telegram_enabled"])
        self.assertTrue(data["telegram_bot_token_configured"])
        self.assertTrue(data["telegram_webhook_secret_configured"])
        self.assertEqual(data["telegram_allowed_chat_ids_text"], "123456\n789000")
        self.assertNotIn("telegram-token-1", str(data))
        self.assertNotIn("telegram-secret-1", str(data))

    def test_admin_settings_masks_feishu_secret_values(self):
        self._login()
        with patch("app.api.routes.configure_telegram_webhook", return_value={"status": "inactive", "message": "noop", "webhook_url": ""}):
            self.client.put(
                "/api/admin/settings",
                json={
                    "feishu_enabled": True,
                    "feishu_app_id": "cli_xxx",
                    "feishu_app_secret": "feishu-app-secret-1",
                    "feishu_verification_token": "verify-token-1",
                    "feishu_encrypt_key": "encrypt-key-1",
                    "feishu_webhook_public_base_url": "https://app.example.com",
                    "feishu_allowed_open_ids": "ou_123\nou_456",
                    "feishu_notify_on_complete": True,
                },
            )

        response = self.client.get("/api/admin/settings")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["feishu_enabled"])
        self.assertTrue(data["feishu_app_secret_configured"])
        self.assertTrue(data["feishu_verification_token_configured"])
        self.assertTrue(data["feishu_encrypt_key_configured"])
        self.assertEqual(data["feishu_allowed_open_ids_text"], "ou_123\nou_456")
        self.assertNotIn("feishu-app-secret-1", str(data))
        self.assertNotIn("verify-token-1", str(data))
        self.assertNotIn("encrypt-key-1", str(data))

    def test_admin_settings_normalizes_feishu_webhook_base_url_when_full_path_is_submitted(self):
        self._login()
        with patch("app.api.routes.configure_telegram_webhook", return_value={"status": "ready", "message": "ok", "webhook_url": "https://wc.example.com/api/integrations/feishu/webhook"}):
            response = self.client.put(
                "/api/admin/settings",
                json={
                    "feishu_enabled": True,
                    "feishu_app_id": "cli_xxx",
                    "feishu_app_secret": "feishu-app-secret-1",
                    "feishu_verification_token": "verify-token-1",
                    "feishu_encrypt_key": "encrypt-key-1",
                    "feishu_webhook_public_base_url": "https://wc.example.com/api/integrations/feishu/webhook",
                    "feishu_allowed_open_ids": "",
                },
            )

        self.assertEqual(response.status_code, 200)
        data = self.client.get("/api/admin/settings").json()
        self.assertEqual(data["feishu_webhook_public_base_url"], "https://wc.example.com")
        self.assertEqual(data["feishu_webhook_url"], "https://wc.example.com/api/integrations/feishu/webhook")

    def test_admin_settings_put_updates_telegram_runtime_config_and_registers_webhook(self):
        self._login()
        with patch("app.api.routes.configure_telegram_webhook", return_value={"status": "success", "message": "registered", "webhook_url": "https://app.example.com/api/integrations/telegram/webhook"}) as mocked_webhook:
            response = self.client.put(
                "/api/admin/settings",
                json={
                    "telegram_enabled": True,
                    "telegram_bot_token": "telegram-token-2",
                    "telegram_webhook_public_base_url": "https://app.example.com",
                    "telegram_webhook_secret": "telegram-secret-2",
                    "telegram_allowed_chat_ids": "10001,10002",
                    "telegram_notify_on_complete": False,
                },
            )

        self.assertEqual(response.status_code, 200)
        mocked_webhook.assert_called_once()
        saved_text = self.runtime_path.read_text(encoding="utf-8")
        self.assertIn("\"enabled\": true", saved_text)
        self.assertIn("\"allowed_chat_ids\": [", saved_text)
        self.assertNotIn("telegram-token-2", saved_text)
        self.assertNotIn("telegram-secret-2", saved_text)
        data = response.json()["settings"]
        self.assertEqual(data["telegram_webhook_status"], "success")
        self.assertEqual(data["telegram_webhook_message"], "registered")

    def test_telegram_webhook_rejects_invalid_secret(self):
        self._login()
        with patch("app.api.routes.configure_telegram_webhook", return_value={"status": "success", "message": "registered", "webhook_url": "https://app.example.com/api/integrations/telegram/webhook"}):
            self.client.put(
                "/api/admin/settings",
                json={
                    "telegram_enabled": True,
                    "telegram_bot_token": "telegram-token",
                    "telegram_webhook_public_base_url": "https://app.example.com",
                    "telegram_webhook_secret": "secret-123",
                    "telegram_allowed_chat_ids": "123456",
                },
            )

        response = self.client.post(
            "/api/integrations/telegram/webhook",
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong-secret"},
            json={"message": {"chat": {"id": 123456}, "text": "https://mp.weixin.qq.com/s/example"}},
        )

        self.assertEqual(response.status_code, 403)

    def test_telegram_webhook_ignores_non_whitelist_chat(self):
        self._login()
        with patch("app.api.routes.configure_telegram_webhook", return_value={"status": "success", "message": "registered", "webhook_url": "https://app.example.com/api/integrations/telegram/webhook"}):
            self.client.put(
                "/api/admin/settings",
                json={
                    "telegram_enabled": True,
                    "telegram_bot_token": "telegram-token",
                    "telegram_webhook_public_base_url": "https://app.example.com",
                    "telegram_webhook_secret": "secret-123",
                    "telegram_allowed_chat_ids": "123456",
                },
            )

        with patch("app.api.routes.send_telegram_message") as mocked_send:
            response = self.client.post(
                "/api/integrations/telegram/webhook",
                headers={"X-Telegram-Bot-Api-Secret-Token": "secret-123"},
                json={"message": {"chat": {"id": 999999}, "text": "https://mp.weixin.qq.com/s/example"}},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ignored")
        mocked_send.assert_not_called()

    def test_telegram_webhook_accepts_single_link_and_submits_background_task(self):
        self._login()
        with patch("app.api.routes.configure_telegram_webhook", return_value={"status": "success", "message": "registered", "webhook_url": "https://app.example.com/api/integrations/telegram/webhook"}):
            self.client.put(
                "/api/admin/settings",
                json={
                    "fns_base_url": "https://fns.example.com",
                    "fns_token": "fns-token",
                    "fns_vault": "obsidian",
                    "telegram_enabled": True,
                    "telegram_bot_token": "telegram-token",
                    "telegram_webhook_public_base_url": "https://app.example.com",
                    "telegram_webhook_secret": "secret-123",
                    "telegram_allowed_chat_ids": "123456",
                    "telegram_notify_on_complete": True,
                },
            )

        with patch("app.api.routes.send_telegram_message") as mocked_send:
            with patch("app.api.routes.submit_telegram_convert_task") as mocked_submit:
                response = self.client.post(
                    "/api/integrations/telegram/webhook",
                    headers={"X-Telegram-Bot-Api-Secret-Token": "secret-123"},
                    json={"message": {"chat": {"id": 123456}, "text": "https://mp.weixin.qq.com/s/example"}},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "accepted")
        mocked_send.assert_called_once()
        mocked_submit.assert_called_once()

    def test_telegram_webhook_accepts_generic_web_link(self):
        self._login()
        with patch("app.api.routes.configure_telegram_webhook", return_value={"status": "success", "message": "registered", "webhook_url": "https://app.example.com/api/integrations/telegram/webhook"}):
            self.client.put(
                "/api/admin/settings",
                json={
                    "fns_base_url": "https://fns.example.com",
                    "fns_token": "fns-token",
                    "fns_vault": "obsidian",
                    "telegram_enabled": True,
                    "telegram_bot_token": "telegram-token",
                    "telegram_webhook_public_base_url": "https://app.example.com",
                    "telegram_webhook_secret": "secret-123",
                    "telegram_allowed_chat_ids": "123456",
                    "telegram_notify_on_complete": True,
                },
            )

        with patch("app.api.routes.send_telegram_message") as mocked_send:
            with patch("app.api.routes.submit_telegram_convert_task") as mocked_submit:
                response = self.client.post(
                    "/api/integrations/telegram/webhook",
                    headers={"X-Telegram-Bot-Api-Secret-Token": "secret-123"},
                    json={"message": {"chat": {"id": 123456}, "text": "https://example.com/post"}},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "accepted")
        mocked_send.assert_called_once()
        mocked_submit.assert_called_once_with("https://example.com/post", "123456")

    def test_telegram_webhook_ignores_duplicate_message(self):
        self._login()
        with patch("app.api.routes.configure_telegram_webhook", return_value={"status": "success", "message": "registered", "webhook_url": "https://app.example.com/api/integrations/telegram/webhook"}):
            self.client.put(
                "/api/admin/settings",
                json={
                    "fns_base_url": "https://fns.example.com",
                    "fns_token": "fns-token",
                    "fns_vault": "obsidian",
                    "telegram_enabled": True,
                    "telegram_bot_token": "telegram-token",
                    "telegram_webhook_public_base_url": "https://app.example.com",
                    "telegram_webhook_secret": "secret-123",
                    "telegram_allowed_chat_ids": "123456",
                },
            )

        payload = {
            "update_id": 9001,
            "message": {
                "message_id": 777,
                "chat": {"id": 123456},
                "text": "https://mp.weixin.qq.com/s/example",
            },
        }
        with patch("builtins.print") as mocked_print:
            with patch("app.api.routes.send_telegram_message") as mocked_send:
                with patch("app.api.routes.submit_telegram_convert_task") as mocked_submit:
                    first = self.client.post(
                        "/api/integrations/telegram/webhook",
                        headers={"X-Telegram-Bot-Api-Secret-Token": "secret-123"},
                        json=payload,
                    )
                    second = self.client.post(
                        "/api/integrations/telegram/webhook",
                        headers={"X-Telegram-Bot-Api-Secret-Token": "secret-123"},
                        json=payload,
                    )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["status"], "accepted")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["status"], "ignored")
        self.assertEqual(second.json()["reason"], "duplicate_message")
        mocked_send.assert_called_once()
        mocked_submit.assert_called_once_with("https://mp.weixin.qq.com/s/example", "123456")
        mocked_print.assert_any_call("[bot] duplicate message ignored platform=telegram key=telegram:123456:777")

    def test_telegram_webhook_replies_error_for_multiple_links(self):
        self._login()
        with patch("app.api.routes.configure_telegram_webhook", return_value={"status": "success", "message": "registered", "webhook_url": "https://app.example.com/api/integrations/telegram/webhook"}):
            self.client.put(
                "/api/admin/settings",
                json={
                    "telegram_enabled": True,
                    "telegram_bot_token": "telegram-token",
                    "telegram_webhook_public_base_url": "https://app.example.com",
                    "telegram_webhook_secret": "secret-123",
                    "telegram_allowed_chat_ids": "123456",
                },
            )

        with patch("app.api.routes.send_telegram_message") as mocked_send:
            with patch("app.api.routes.submit_telegram_convert_task") as mocked_submit:
                response = self.client.post(
                    "/api/integrations/telegram/webhook",
                    headers={"X-Telegram-Bot-Api-Secret-Token": "secret-123"},
                    json={
                        "message": {
                            "chat": {"id": 123456},
                            "text": "https://mp.weixin.qq.com/s/one https://mp.weixin.qq.com/s/two",
                        }
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "replied")
        mocked_send.assert_called_once()
        mocked_submit.assert_not_called()

    def test_feishu_webhook_accepts_url_verification(self):
        self._login()
        with patch("app.api.routes.configure_telegram_webhook", return_value={"status": "inactive", "message": "noop", "webhook_url": ""}):
            self.client.put(
                "/api/admin/settings",
                json={
                    "feishu_enabled": True,
                    "feishu_app_id": "cli_xxx",
                    "feishu_app_secret": "secret",
                    "feishu_verification_token": "verify-token",
                    "feishu_encrypt_key": "encrypt-key",
                    "feishu_webhook_public_base_url": "https://app.example.com",
                    "feishu_allowed_open_ids": "ou_123",
                },
            )

        response = self.client.post(
            "/api/integrations/feishu/webhook",
            json={"type": "url_verification", "challenge": "challenge-123", "token": "verify-token"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["challenge"], "challenge-123")

    def test_feishu_webhook_logs_sanitized_verification_payload(self):
        self._login()
        with patch("app.api.routes.configure_telegram_webhook", return_value={"status": "inactive", "message": "noop", "webhook_url": ""}):
            self.client.put(
                "/api/admin/settings",
                json={
                    "feishu_enabled": True,
                    "feishu_app_id": "cli_xxx",
                    "feishu_app_secret": "secret",
                    "feishu_verification_token": "verify-token",
                    "feishu_encrypt_key": "encrypt-key",
                    "feishu_webhook_public_base_url": "https://app.example.com",
                    "feishu_allowed_open_ids": "",
                },
            )

        with patch("builtins.print") as mocked_print:
            response = self.client.post(
                "/api/integrations/feishu/webhook",
                json={"type": "url_verification", "challenge": "challenge-123", "token": "verify-token"},
            )

        self.assertEqual(response.status_code, 200)
        mocked_print.assert_any_call(
            "[feishu] webhook payload={'type': 'url_verification', 'challenge': 'challenge-123', 'token': '***'}"
        )

    def test_feishu_webhook_rejects_invalid_verification_token(self):
        self._login()
        with patch("app.api.routes.configure_telegram_webhook", return_value={"status": "inactive", "message": "noop", "webhook_url": ""}):
            self.client.put(
                "/api/admin/settings",
                json={
                    "feishu_enabled": True,
                    "feishu_app_id": "cli_xxx",
                    "feishu_app_secret": "secret",
                    "feishu_verification_token": "verify-token",
                    "feishu_encrypt_key": "encrypt-key",
                    "feishu_webhook_public_base_url": "https://app.example.com",
                    "feishu_allowed_open_ids": "ou_123",
                },
            )

        response = self.client.post(
            "/api/integrations/feishu/webhook",
            json={"type": "url_verification", "challenge": "challenge-123", "token": "wrong-token"},
        )

        self.assertEqual(response.status_code, 403)

    def test_feishu_webhook_ignores_non_whitelist_open_id(self):
        self._login()
        with patch("app.api.routes.configure_telegram_webhook", return_value={"status": "inactive", "message": "noop", "webhook_url": ""}):
            self.client.put(
                "/api/admin/settings",
                json={
                    "feishu_enabled": True,
                    "feishu_app_id": "cli_xxx",
                    "feishu_app_secret": "secret",
                    "feishu_verification_token": "verify-token",
                    "feishu_encrypt_key": "encrypt-key",
                    "feishu_webhook_public_base_url": "https://app.example.com",
                    "feishu_allowed_open_ids": "ou_123",
                },
            )

        with patch("app.api.routes.send_feishu_message") as mocked_send:
            response = self.client.post(
                "/api/integrations/feishu/webhook",
                json={
                    "schema": "2.0",
                    "header": {"event_type": "im.message.receive_v1"},
                    "event": {
                        "message": {
                            "message_type": "text",
                            "chat_type": "p2p",
                            "content": "{\"text\":\"https://mp.weixin.qq.com/s/example\"}",
                        },
                        "sender": {"sender_id": {"open_id": "ou_not_allowed"}},
                    },
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ignored")
        mocked_send.assert_not_called()

    def test_feishu_webhook_accepts_single_link_and_submits_background_task(self):
        self._login()
        with patch("app.api.routes.configure_telegram_webhook", return_value={"status": "inactive", "message": "noop", "webhook_url": ""}):
            self.client.put(
                "/api/admin/settings",
                json={
                    "feishu_enabled": True,
                    "feishu_app_id": "cli_xxx",
                    "feishu_app_secret": "secret",
                    "feishu_verification_token": "verify-token",
                    "feishu_encrypt_key": "encrypt-key",
                    "feishu_webhook_public_base_url": "https://app.example.com",
                    "feishu_allowed_open_ids": "ou_123",
                    "feishu_notify_on_complete": True,
                    "fns_base_url": "https://fns.example.com",
                    "fns_token": "fns-token",
                    "fns_vault": "MainVault",
                },
            )

        with patch("app.api.routes.send_feishu_message") as mocked_send:
            with patch("app.api.routes.submit_feishu_convert_task") as mocked_submit:
                response = self.client.post(
                    "/api/integrations/feishu/webhook",
                    json={
                        "schema": "2.0",
                        "header": {"event_type": "im.message.receive_v1"},
                        "event": {
                            "message": {
                                "message_type": "text",
                                "chat_type": "p2p",
                                "content": "{\"text\":\"https://mp.weixin.qq.com/s/example\"}",
                            },
                            "sender": {"sender_id": {"open_id": "ou_123"}},
                        },
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "accepted")
        mocked_send.assert_called_once()
        mocked_submit.assert_called_once_with("https://mp.weixin.qq.com/s/example", "ou_123")

    def test_feishu_webhook_rejects_zhihu_link(self):
        self._login()
        with patch("app.api.routes.configure_telegram_webhook", return_value={"status": "inactive", "message": "noop", "webhook_url": ""}):
            self.client.put(
                "/api/admin/settings",
                json={
                    "feishu_enabled": True,
                    "feishu_app_id": "cli_xxx",
                    "feishu_app_secret": "secret",
                    "feishu_verification_token": "verify-token",
                    "feishu_encrypt_key": "encrypt-key",
                    "feishu_webhook_public_base_url": "https://app.example.com",
                    "feishu_allowed_open_ids": "ou_123",
                    "feishu_notify_on_complete": True,
                    "fns_base_url": "https://fns.example.com",
                    "fns_token": "fns-token",
                    "fns_vault": "MainVault",
                },
            )

        with patch("app.api.routes.send_feishu_message") as mocked_send:
            with patch("app.api.routes.submit_feishu_convert_task") as mocked_submit:
                response = self.client.post(
                    "/api/integrations/feishu/webhook",
                    json={
                        "schema": "2.0",
                        "header": {"event_type": "im.message.receive_v1"},
                        "event": {
                            "message": {
                                "message_type": "text",
                                "chat_type": "p2p",
                                "content": "{\"text\":\"https://zhuanlan.zhihu.com/p/123456\"}",
                            },
                            "sender": {"sender_id": {"open_id": "ou_123"}},
                        },
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "replied")
        self.assertEqual(response.json()["reason"], "no_link")
        mocked_send.assert_called_once()
        mocked_submit.assert_not_called()

    def test_feishu_webhook_ignores_duplicate_message(self):
        self._login()
        with patch("app.api.routes.configure_telegram_webhook", return_value={"status": "inactive", "message": "noop", "webhook_url": ""}):
            self.client.put(
                "/api/admin/settings",
                json={
                    "feishu_enabled": True,
                    "feishu_app_id": "cli_xxx",
                    "feishu_app_secret": "secret",
                    "feishu_verification_token": "verify-token",
                    "feishu_encrypt_key": "encrypt-key",
                    "feishu_webhook_public_base_url": "https://app.example.com",
                    "feishu_allowed_open_ids": "ou_123",
                    "fns_base_url": "https://fns.example.com",
                    "fns_token": "fns-token",
                    "fns_vault": "MainVault",
                },
            )

        payload = {
            "schema": "2.0",
            "header": {"event_type": "im.message.receive_v1", "event_id": "evt_1001"},
            "event": {
                "message": {
                    "message_id": "om_1001",
                    "message_type": "text",
                    "chat_type": "p2p",
                    "content": "{\"text\":\"https://mp.weixin.qq.com/s/example\"}",
                },
                "sender": {"sender_id": {"open_id": "ou_123"}},
            },
        }
        with patch("builtins.print") as mocked_print:
            with patch("app.api.routes.send_feishu_message") as mocked_send:
                with patch("app.api.routes.submit_feishu_convert_task") as mocked_submit:
                    first = self.client.post("/api/integrations/feishu/webhook", json=payload)
                    second = self.client.post("/api/integrations/feishu/webhook", json=payload)

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["status"], "accepted")
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["status"], "ignored")
        self.assertEqual(second.json()["reason"], "duplicate_message")
        mocked_send.assert_called_once()
        mocked_submit.assert_called_once_with("https://mp.weixin.qq.com/s/example", "ou_123")
        mocked_print.assert_any_call("[bot] duplicate message ignored platform=feishu key=feishu:evt_1001")

    def test_feishu_webhook_accepts_single_link_when_whitelist_empty(self):
        self._login()
        with patch("app.api.routes.configure_telegram_webhook", return_value={"status": "inactive", "message": "noop", "webhook_url": ""}):
            response = self.client.put(
                "/api/admin/settings",
                json={
                    "feishu_enabled": True,
                    "feishu_app_id": "cli_xxx",
                    "feishu_app_secret": "secret",
                    "feishu_verification_token": "verify-token",
                    "feishu_encrypt_key": "encrypt-key",
                    "feishu_webhook_public_base_url": "https://app.example.com",
                    "feishu_allowed_open_ids": "",
                    "feishu_notify_on_complete": True,
                    "fns_base_url": "https://fns.example.com",
                    "fns_token": "fns-token",
                    "fns_vault": "MainVault",
                },
            )

        self.assertEqual(response.status_code, 200)
        with patch("app.api.routes.send_feishu_message") as mocked_send:
            with patch("app.api.routes.submit_feishu_convert_task") as mocked_submit:
                response = self.client.post(
                    "/api/integrations/feishu/webhook",
                    json={
                        "schema": "2.0",
                        "header": {"event_type": "im.message.receive_v1"},
                        "event": {
                            "message": {
                                "message_type": "text",
                                "chat_type": "p2p",
                                "content": "{\"text\":\"https://mp.weixin.qq.com/s/example\"}",
                            },
                            "sender": {"sender_id": {"open_id": "ou_bootstrap"}},
                        },
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "accepted")
        mocked_send.assert_called_once()
        mocked_submit.assert_called_once_with("https://mp.weixin.qq.com/s/example", "ou_bootstrap")

    def test_feishu_webhook_logs_reply_failure_instead_of_returning_500(self):
        self._login()
        with patch("app.api.routes.configure_telegram_webhook", return_value={"status": "inactive", "message": "noop", "webhook_url": ""}):
            self.client.put(
                "/api/admin/settings",
                json={
                    "feishu_enabled": True,
                    "feishu_app_id": "cli_xxx",
                    "feishu_app_secret": "secret",
                    "feishu_verification_token": "verify-token",
                    "feishu_encrypt_key": "encrypt-key",
                    "feishu_webhook_public_base_url": "https://app.example.com",
                    "feishu_allowed_open_ids": "",
                },
            )

        with patch("builtins.print") as mocked_print:
            with patch("app.api.routes.send_feishu_message", side_effect=RuntimeError("飞书发送消息失败: 400 bad request")):
                response = self.client.post(
                    "/api/integrations/feishu/webhook",
                    json={
                        "schema": "2.0",
                        "header": {"event_type": "im.message.receive_v1"},
                        "event": {
                            "message": {
                                "message_type": "text",
                                "chat_type": "p2p",
                                "content": "{\"text\":\"hello\"}",
                            },
                            "sender": {"sender_id": {"open_id": "ou_bootstrap"}},
                        },
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "replied")
        mocked_print.assert_any_call("[feishu] send message failed open_id=ou_bootstrap: 飞书发送消息失败: 400 bad request")

    def test_feishu_webhook_logs_open_id_to_stdout(self):
        self._login()
        with patch("app.api.routes.configure_telegram_webhook", return_value={"status": "inactive", "message": "noop", "webhook_url": ""}):
            self.client.put(
                "/api/admin/settings",
                json={
                    "feishu_enabled": True,
                    "feishu_app_id": "cli_xxx",
                    "feishu_app_secret": "secret",
                    "feishu_verification_token": "verify-token",
                    "feishu_encrypt_key": "encrypt-key",
                    "feishu_webhook_public_base_url": "https://app.example.com",
                    "feishu_allowed_open_ids": "ou_123",
                    "fns_base_url": "https://fns.example.com",
                    "fns_token": "fns-token",
                    "fns_vault": "MainVault",
                },
            )

        with patch("builtins.print") as mocked_print:
            with patch("app.api.routes.send_feishu_message"):
                with patch("app.api.routes.submit_feishu_convert_task"):
                    response = self.client.post(
                        "/api/integrations/feishu/webhook",
                        json={
                            "schema": "2.0",
                            "header": {"event_type": "im.message.receive_v1"},
                            "event": {
                                "message": {
                                    "message_type": "text",
                                    "chat_type": "p2p",
                                    "content": "{\"text\":\"https://mp.weixin.qq.com/s/example\"}",
                                },
                                "sender": {"sender_id": {"open_id": "ou_123"}},
                            },
                        },
                    )

        self.assertEqual(response.status_code, 200)
        mocked_print.assert_any_call("[feishu] received message open_id=ou_123 chat_type=p2p")

    def test_feishu_webhook_replies_error_for_multiple_links(self):
        self._login()
        with patch("app.api.routes.configure_telegram_webhook", return_value={"status": "inactive", "message": "noop", "webhook_url": ""}):
            self.client.put(
                "/api/admin/settings",
                json={
                    "feishu_enabled": True,
                    "feishu_app_id": "cli_xxx",
                    "feishu_app_secret": "secret",
                    "feishu_verification_token": "verify-token",
                    "feishu_encrypt_key": "encrypt-key",
                    "feishu_webhook_public_base_url": "https://app.example.com",
                    "feishu_allowed_open_ids": "ou_123",
                    "fns_base_url": "https://fns.example.com",
                    "fns_token": "fns-token",
                    "fns_vault": "MainVault",
                },
            )

        with patch("app.api.routes.send_feishu_message") as mocked_send:
            with patch("app.api.routes.submit_feishu_convert_task") as mocked_submit:
                response = self.client.post(
                    "/api/integrations/feishu/webhook",
                    json={
                        "schema": "2.0",
                        "header": {"event_type": "im.message.receive_v1"},
                        "event": {
                            "message": {
                                "message_type": "text",
                                "chat_type": "p2p",
                                "content": "{\"text\":\"https://mp.weixin.qq.com/s/one https://mp.weixin.qq.com/s/two\"}",
                            },
                            "sender": {"sender_id": {"open_id": "ou_123"}},
                        },
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "replied")
        mocked_send.assert_called_once()
        mocked_submit.assert_not_called()

    def test_process_telegram_convert_task_reports_s3_image_mode_when_payload_omits_it(self):
        settings = SimpleNamespace(
            default_timeout=30,
            telegram_notify_on_complete=True,
            image_mode="s3_hotlink",
        )
        payload = {
            "result": {"title": "标题"},
            "sync": {"path": "00_Inbox/微信公众号/标题.md"},
        }

        with patch("app.services.get_settings", return_value=settings):
            with patch("app.services.execute_single_conversion", return_value=payload) as mocked_execute:
                with patch("app.services.send_telegram_message") as mocked_send:
                    process_telegram_convert_task("https://mp.weixin.qq.com/s/example", "123456")

        mocked_send.assert_called_once()
        message = mocked_send.call_args.args[1]
        self.assertIn("图片模式：S3 图床外链", message)
        self.assertTrue(mocked_execute.call_args.kwargs["require_ai_success"])

    def test_process_telegram_convert_task_reports_ai_failure_without_success_message(self):
        settings = SimpleNamespace(
            default_timeout=30,
            telegram_notify_on_complete=True,
            image_mode="s3_hotlink",
        )

        with patch("app.services.get_settings", return_value=settings):
            with patch("app.services.execute_single_conversion", side_effect=RuntimeError("AI 润色失败：模板未成功应用")) as mocked_execute:
                with patch("app.services.send_telegram_message") as mocked_send:
                    process_telegram_convert_task("https://mp.weixin.qq.com/s/example", "123456")

        mocked_send.assert_called_once()
        self.assertIn("转换失败：AI 润色失败：模板未成功应用", mocked_send.call_args.args[1])
        self.assertTrue(mocked_execute.call_args.kwargs["require_ai_success"])

    def test_process_feishu_convert_task_reports_s3_image_mode_when_payload_omits_it(self):
        settings = SimpleNamespace(
            default_timeout=30,
            feishu_notify_on_complete=True,
            image_mode="s3_hotlink",
        )
        payload = {
            "result": {"title": "标题"},
            "sync": {"path": "00_Inbox/微信公众号/标题.md"},
        }

        with patch("app.services.get_settings", return_value=settings):
            with patch("app.services.execute_single_conversion", return_value=payload) as mocked_execute:
                with patch("app.services.send_feishu_message") as mocked_send:
                    process_feishu_convert_task("https://mp.weixin.qq.com/s/example", "ou_123")

        mocked_send.assert_called_once()
        message = mocked_send.call_args.args[1]
        self.assertIn("图片模式：S3 图床外链", message)
        self.assertTrue(mocked_execute.call_args.kwargs["require_ai_success"])

    def test_process_feishu_convert_task_reports_ai_failure_without_success_message(self):
        settings = SimpleNamespace(
            default_timeout=30,
            feishu_notify_on_complete=True,
            image_mode="s3_hotlink",
        )

        with patch("app.services.get_settings", return_value=settings):
            with patch("app.services.execute_single_conversion", side_effect=RuntimeError("AI 润色失败：模板未成功应用")) as mocked_execute:
                with patch("app.services.send_feishu_message") as mocked_send:
                    process_feishu_convert_task("https://mp.weixin.qq.com/s/example", "ou_123")

        mocked_send.assert_called_once()
        self.assertIn("转换失败：AI 润色失败：模板未成功应用", mocked_send.call_args.args[1])
        self.assertTrue(mocked_execute.call_args.kwargs["require_ai_success"])

    def test_settings_requires_login_redirect(self):
        response = self.client.get("/settings", follow_redirects=False)

        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/login")

    def test_admin_settings_masks_secret_values(self):
        self._login()
        self.client.put(
            "/api/admin/settings",
            json={
                "fns_base_url": "https://fns.example.com",
                "fns_token": "fns-secret-token",
                "fns_vault": "MainVault",
                "fns_target_dir": "00_Inbox/微信公众号",
                "image_mode": "s3_hotlink",
                "image_storage_endpoint": "https://s3.example.com",
                "image_storage_region": "auto",
                "image_storage_bucket": "bucket-a",
                "image_storage_access_key_id": "key-1",
                "image_storage_secret_access_key": "secret-1",
                "image_storage_path_template": "wechat/{year}/{filename}",
                "image_storage_public_base_url": "https://img.example.com",
                "ai_enabled": True,
                "ai_providers": [
                    {
                        "id": "openai-compatible-default",
                        "type": "openai_compatible",
                        "display_name": "OpenAI Compatible",
                        "built_in": True,
                        "enabled": True,
                        "base_url": "https://api.example.com/v1",
                        "api_key": "ai-secret-key",
                    }
                ],
                "ai_models": [
                    {
                        "id": "model-openai-compatible-gpt54mini",
                        "provider_id": "openai-compatible-default",
                        "display_name": "gpt-5.4-mini",
                        "model_id": "gpt-5.4-mini",
                        "enabled": True,
                    }
                ],
                "ai_selected_model_id": "model-openai-compatible-gpt54mini",
                "ai_context_template": "{{content}}",
                "ai_template_source": "manual",
            },
        )
        response = self.client.get("/api/admin/settings")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["fns_base_url"], "https://fns.example.com")
        self.assertEqual(data["current_user"]["username"], "admin")
        self.assertTrue(data["fns_token_configured"])
        self.assertEqual(data["image_mode"], "s3_hotlink")
        self.assertEqual(data["image_storage_endpoint"], "https://s3.example.com")
        self.assertTrue(data["image_storage_secret_access_key_configured"])
        self.assertTrue(data["ai_enabled"])
        self.assertEqual(data["ai_model"], "gpt-5.4-mini")
        self.assertEqual(data["ai_selected_model_id"], "model-openai-compatible-gpt54mini")
        self.assertEqual(data["ai_selected_provider"]["type"], "openai_compatible")
        self.assertEqual(data["ai_providers"][0]["base_url"], "https://api.example.com/v1")
        self.assertTrue(data["ai_providers"][0]["api_key_configured"])
        self.assertEqual(data["ai_context_template"], "{{content}}")
        self.assertEqual(data["ai_template_source"], "manual")
        self.assertNotIn("fns-secret-token", str(data))
        self.assertNotIn("secret-1", str(data))
        self.assertNotIn("ai-secret-key", str(data))
        self.assertNotIn("access_token_configured", data)

    def test_admin_settings_put_updates_runtime_config_and_config_endpoint(self):
        self._login()
        save_response = self.client.put(
            "/api/admin/settings",
            json={
                "fns_base_url": "https://obsync.example.com",
                "fns_token": "new-fns-token",
                "fns_vault": "obsidian",
                "fns_target_dir": "00_Inbox/微信公众号",
                "image_mode": "s3_hotlink",
                "image_storage_endpoint": "https://s3.example.com",
                "image_storage_region": "auto",
                "image_storage_bucket": "bucket-a",
                "image_storage_access_key_id": "key-1",
                "image_storage_secret_access_key": "secret-1",
                "image_storage_path_template": "wechat/{year}/{filename}",
                "image_storage_public_base_url": "https://img.example.com",
                "ai_enabled": True,
                "ai_providers": [
                    {
                        "id": "openai-compatible-default",
                        "type": "openai_compatible",
                        "display_name": "OpenAI Compatible",
                        "built_in": True,
                        "enabled": True,
                        "base_url": "https://api.example.com/v1",
                        "api_key": "ai-key-1",
                    }
                ],
                "ai_models": [
                    {
                        "id": "model-openai-compatible-gpt54mini",
                        "provider_id": "openai-compatible-default",
                        "display_name": "gpt-5.4-mini",
                        "model_id": "gpt-5.4-mini",
                        "enabled": True,
                    }
                ],
                "ai_selected_model_id": "model-openai-compatible-gpt54mini",
                "ai_prompt_template": "请总结 {{title}}",
                "ai_frontmatter_template": "---\ntitle: {{title}}\nsummary: {{summary}}\n---",
                "ai_body_template": "> [!summary]\n> {{summary}}",
                "ai_context_template": "{{title}}\n\n{{content}}",
                "ai_template_source": "clipper_import",
            },
        )
        config_response = self.client.get("/api/config")

        self.assertEqual(save_response.status_code, 200)
        self.assertTrue(self.runtime_path.exists())
        saved_text = self.runtime_path.read_text(encoding="utf-8")
        self.assertIn("\"auth\"", saved_text)
        self.assertIn("\"user_settings\"", saved_text)
        self.assertIn("https://obsync.example.com", saved_text)
        self.assertIn("\"image_storage\"", saved_text)
        self.assertIn("\"image_mode\": \"s3_hotlink\"", saved_text)
        self.assertNotIn("new-fns-token", saved_text)
        self.assertNotIn("secret-1", saved_text)
        self.assertNotIn("ai-key-1", saved_text)
        config_data = config_response.json()
        self.assertTrue(config_data["fns_enabled"])
        self.assertEqual(config_data["fns_base_url"], "https://obsync.example.com")
        self.assertEqual(config_data["image_mode"], "s3_hotlink")
        self.assertEqual(config_data["image_public_base_url"], "https://img.example.com")
        self.assertTrue(config_data["ai_enabled"])
        self.assertTrue(config_data["ai_configured"])
        self.assertEqual(config_data["ai_model"], "gpt-5.4-mini")
        self.assertEqual(config_data["ai_selected_provider"], "openai_compatible")
        self.assertEqual(config_data["ai_template_source"], "clipper_import")

    def test_ai_test_endpoint_uses_current_form_payload(self):
        self._login()
        with patch(
            "app.api.routes.test_ai_connectivity",
            return_value={
                "success": True,
                "latency_ms": 123,
                "model": "gpt-5.4-mini",
                "preview": "{\"pong\":\"ok\"}",
                "message": "连接正常",
            },
        ) as mocked_test:
            response = self.client.post(
                "/api/admin/ai-test",
                json={
                    "provider": {
                        "id": "openai-compatible-default",
                        "type": "openai_compatible",
                        "display_name": "OpenAI Compatible",
                        "base_url": "https://api.example.com/v1",
                        "api_key": "ai-key-1",
                        "enabled": True,
                        "built_in": True,
                    },
                    "model": {
                        "id": "model-openai-compatible-gpt54mini",
                        "provider_id": "openai-compatible-default",
                        "display_name": "gpt-5.4-mini",
                        "model_id": "gpt-5.4-mini",
                        "enabled": True,
                    },
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertEqual(mocked_test.call_args.kwargs["provider"]["base_url"], "https://api.example.com/v1")
        self.assertEqual(mocked_test.call_args.kwargs["provider"]["api_key"], "ai-key-1")
        self.assertEqual(mocked_test.call_args.kwargs["model"]["model_id"], "gpt-5.4-mini")

    def test_ai_test_endpoint_uses_selected_saved_model_when_request_is_empty(self):
        self._login()
        self.client.put(
            "/api/admin/settings",
            json={
                "ai_enabled": True,
                "ai_providers": [
                    {
                        "id": "openai-compatible-default",
                        "type": "openai_compatible",
                        "display_name": "OpenAI Compatible",
                        "built_in": True,
                        "enabled": True,
                        "base_url": "https://api.example.com/v1",
                        "api_key": "saved-key",
                    }
                ],
                "ai_models": [
                    {
                        "id": "saved-model",
                        "provider_id": "openai-compatible-default",
                        "display_name": "gpt-5.4-mini",
                        "model_id": "gpt-5.4-mini",
                        "enabled": True,
                    }
                ],
                "ai_selected_model_id": "saved-model",
                "ai_prompt_template": '{"summary":"一句话总结"}',
                "ai_frontmatter_template": "---\ntitle: {{title}}\n---",
                "ai_body_template": "{{content}}",
                "ai_context_template": "{{content}}",
            },
        )
        with patch(
            "app.api.routes.test_ai_connectivity",
            return_value={
                "success": True,
                "latency_ms": 123,
                "model": "gpt-5.4-mini",
                "preview": "{\"pong\":\"ok\"}",
                "message": "连接正常",
            },
        ) as mocked_test:
            response = self.client.post("/api/admin/ai-test", json={})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertEqual(mocked_test.call_args.kwargs["provider"]["id"], "openai-compatible-default")
        self.assertEqual(mocked_test.call_args.kwargs["model"]["id"], "saved-model")

    def test_ai_test_endpoint_merges_saved_api_key_when_form_provider_leaves_it_blank(self):
        self._login()
        self.client.put(
            "/api/admin/settings",
            json={
                "ai_enabled": True,
                "ai_providers": [
                    {
                        "id": "openai-compatible-default",
                        "type": "openai_compatible",
                        "display_name": "OpenAI Compatible",
                        "built_in": True,
                        "enabled": True,
                        "base_url": "https://api.example.com/v1",
                        "api_key": "saved-key",
                    }
                ],
                "ai_models": [
                    {
                        "id": "saved-model",
                        "provider_id": "openai-compatible-default",
                        "display_name": "gpt-5.4-mini",
                        "model_id": "gpt-5.4-mini",
                        "enabled": True,
                    }
                ],
                "ai_selected_model_id": "saved-model",
            },
        )
        with patch(
            "app.api.routes.test_ai_connectivity",
            return_value={
                "success": True,
                "latency_ms": 88,
                "model": "gpt-5.4-mini",
                "preview": "{\"pong\":\"ok\"}",
                "message": "连接正常",
            },
        ) as mocked_test:
            response = self.client.post(
                "/api/admin/ai-test",
                json={
                    "provider": {
                        "id": "openai-compatible-default",
                        "type": "openai_compatible",
                        "display_name": "OpenAI Compatible",
                        "built_in": True,
                        "enabled": True,
                        "base_url": "https://api.example.com/v1",
                        "api_key": "",
                    },
                    "model": {
                        "id": "saved-model",
                        "provider_id": "openai-compatible-default",
                        "display_name": "gpt-5.4-mini",
                        "model_id": "gpt-5.4-mini",
                        "enabled": True,
                    },
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(mocked_test.call_args.kwargs["provider"]["api_key"], "saved-key")

    def test_ai_selected_model_can_be_updated_immediately(self):
        self._login()
        self.client.put(
            "/api/admin/settings",
            json={
                "ai_enabled": True,
                "ai_providers": [
                    {
                        "id": "openai-compatible-default",
                        "type": "openai_compatible",
                        "display_name": "OpenAI Compatible",
                        "built_in": True,
                        "enabled": True,
                        "base_url": "https://api.example.com/v1",
                        "api_key": "saved-key",
                    }
                ],
                "ai_models": [
                    {
                        "id": "model-a",
                        "provider_id": "openai-compatible-default",
                        "display_name": "gpt-5.4-mini",
                        "model_id": "gpt-5.4-mini",
                        "enabled": True,
                    },
                    {
                        "id": "model-b",
                        "provider_id": "openai-compatible-default",
                        "display_name": "gpt-5.4-nano",
                        "model_id": "gpt-5.4-nano",
                        "enabled": True,
                    },
                ],
                "ai_selected_model_id": "model-a",
            },
        )

        response = self.client.post("/api/admin/ai-selection", json={"ai_selected_model_id": "model-b"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ai_selected_model_id"], "model-b")
        config_response = self.client.get("/api/admin/settings")
        self.assertEqual(config_response.status_code, 200)
        self.assertEqual(config_response.json()["ai_selected_model_id"], "model-b")

    def test_ai_selected_model_update_rejects_unknown_model(self):
        self._login()
        response = self.client.post("/api/admin/ai-selection", json={"ai_selected_model_id": "missing-model"})

        self.assertEqual(response.status_code, 400)

    def test_admin_settings_accepts_wechat_hotlink_without_storage_fields(self):
        self._login()
        response = self.client.put(
            "/api/admin/settings",
            json={"image_mode": "wechat_hotlink"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()["settings"]
        self.assertEqual(data["image_mode"], "wechat_hotlink")
        self.assertFalse(data["image_storage_enabled"])

    def test_admin_fns_status_returns_connection_summary(self):
        self._login()
        self.client.put(
            "/api/admin/settings",
            json={
                "fns_base_url": "https://obsync.example.com",
                "fns_token": "fns-token",
                "fns_vault": "obsidian",
            },
        )
        fake_status = {
            "configured": True,
            "connected": True,
            "user": {"username": "luluen"},
            "vault_exists": True,
            "vault_name": "obsidian",
            "vault_count": 1,
        }
        with patch("app.api.routes.check_fns_status", return_value=fake_status):
            response = self.client.get("/api/admin/fns-status")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["connected"])
        self.assertTrue(data["vault_exists"])
        self.assertEqual(data["user"]["username"], "luluen")

    def test_change_password_invalidates_old_password(self):
        self._login()
        change_response = self.client.put(
            "/api/admin/password",
            json={"current_password": "admin", "new_password": "new-secret"},
        )
        old_login = self.client.post("/api/session", json={"username": "admin", "password": "admin"})
        new_login = self.client.post("/api/session", json={"username": "admin", "password": "new-secret"})

        self.assertEqual(change_response.status_code, 200)
        self.assertEqual(old_login.status_code, 401)
        self.assertEqual(new_login.status_code, 200)

    def test_offline_reset_invalidates_existing_session_cookie(self):
        self._login()
        settings = get_settings()
        cookie_value = build_session_token(settings.username, settings.password_hash, settings.session_secret)

        reset_admin_credentials(new_password="offline-secret")

        response = self.client.get(
            "/api/config",
            cookies={"wechat_md_session": cookie_value},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self._login(password="offline-secret").status_code, 200)


if __name__ == "__main__":
    unittest.main()
