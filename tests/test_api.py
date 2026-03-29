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
from app.services import extract_single_wechat_url, parse_links, process_telegram_convert_task  # noqa: E402


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
            with patch("app.services.run_pipeline", return_value=fake_result):
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

        def fake_run_pipeline(url, output_base_dir, save_html, timeout):
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

        with patch("app.services.run_pipeline", side_effect=fake_run_pipeline):
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

        def fake_run_pipeline(url, output_base_dir, save_html, timeout):
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

        with patch("app.services.run_pipeline", side_effect=fake_run_pipeline):
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
        self.assertIn("Bot Token", text)
        self.assertIn("Webhook 对外基础地址", text)
        self.assertIn("白名单 Chat ID", text)
        self.assertIn("AI 润色", text)
        self.assertIn("OpenAI 兼容 Base URL", text)
        self.assertIn("解释器提示词", text)
        self.assertIn("frontmatter 模板", text)
        self.assertIn("body 模板", text)
        self.assertIn("测试 AI 连通性", text)
        self.assertIn("导入 Clipper JSON 模板", text)
        self.assertIn('id="clipper-json-file"', text)
        self.assertNotIn('id="paste-clipper-btn"', text)

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
            with patch("app.services.execute_single_conversion", return_value=payload):
                with patch("app.services.send_telegram_message") as mocked_send:
                    process_telegram_convert_task("https://mp.weixin.qq.com/s/example", "123456")

        mocked_send.assert_called_once()
        message = mocked_send.call_args.args[1]
        self.assertIn("图片模式：S3 图床外链", message)

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
                "ai_base_url": "https://api.example.com/v1",
                "ai_api_key": "ai-secret-key",
                "ai_model": "gpt-5.4-mini",
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
        self.assertTrue(data["ai_api_key_configured"])
        self.assertEqual(data["ai_model"], "gpt-5.4-mini")
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
                "ai_base_url": "https://api.example.com/v1",
                "ai_api_key": "ai-key-1",
                "ai_model": "gpt-5.4-mini",
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
                    "base_url": "https://api.example.com/v1",
                    "api_key": "ai-key-1",
                    "model": "gpt-5.4-mini",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        self.assertEqual(mocked_test.call_args.kwargs["base_url"], "https://api.example.com/v1")
        self.assertEqual(mocked_test.call_args.kwargs["api_key"], "ai-key-1")
        self.assertEqual(mocked_test.call_args.kwargs["model"], "gpt-5.4-mini")

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
