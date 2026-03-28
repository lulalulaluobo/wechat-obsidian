import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.main import app  # noqa: E402


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        runtime_path = Path(self.temp_dir.name) / "runtime-config.json"
        self.env_patcher = patch.dict(
            os.environ,
            {"WECHAT_MD_RUNTIME_CONFIG_PATH": str(runtime_path)},
            clear=False,
        )
        self.env_patcher.start()
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
        self.assertIn("登录 Wechat MD Server", response.text)
        self.assertIn("用户名", response.text)
        self.assertIn("密码", response.text)

    def test_default_admin_login_success(self):
        login_response = self._login()
        config_response = self.client.get("/api/config")

        self.assertEqual(login_response.status_code, 200)
        self.assertEqual(config_response.status_code, 200)

    def test_index_page_is_fns_only_after_login(self):
        self._login()
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn("同步到当前 FNS", response.text)
        self.assertNotIn("输出目录", response.text)
        self.assertNotIn("输出目标", response.text)
        self.assertNotIn("写入本地目录", response.text)

    def test_wrong_password_is_rejected(self):
        response = self._login(password="wrong-password")

        self.assertEqual(response.status_code, 401)

    def test_logout_blocks_protected_endpoints(self):
        self._login()
        self.client.delete("/api/session")

        response = self.client.get("/api/config")

        self.assertEqual(response.status_code, 401)

    def test_convert_success(self):
        self._login()
        fake_result = {"title": "示例", "markdown_file": r"D:\obsidian\00_Inbox\01_示例\示例.md"}
        with patch("app.api.routes.run_pipeline", return_value=fake_result):
            response = self.client.post("/api/convert", json={"url": "https://mp.weixin.qq.com/s/example"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        self.assertEqual(response.json()["result"]["title"], "示例")

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
            with patch("app.api.routes.run_pipeline", return_value=fake_result):
                with patch("app.api.routes.sync_result_to_output", return_value=fake_sync) as mocked_sync:
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

    def test_settings_page_contains_fns_import_actions_when_logged_in(self):
        self._login()
        response = self.client.get("/settings")

        self.assertEqual(response.status_code, 200)
        text = response.text
        self.assertIn("从剪贴板导入 FNS", text)
        self.assertIn("解析并填充", text)
        self.assertIn('id="fns-json-input"', text)
        self.assertIn("顶部概览", text)
        self.assertIn("检测 FNS 连接", text)
        self.assertIn('id="fns-status-result"', text)
        self.assertIn("当前登录用户", text)
        self.assertIn("修改密码", text)

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
            },
        )
        response = self.client.get("/api/admin/settings")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["fns_base_url"], "https://fns.example.com")
        self.assertEqual(data["current_user"]["username"], "admin")
        self.assertTrue(data["fns_token_configured"])
        self.assertNotIn("fns-secret-token", str(data))
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
            },
        )
        config_response = self.client.get("/api/config")

        self.assertEqual(save_response.status_code, 200)
        self.assertTrue(self.runtime_path.exists())
        saved_text = self.runtime_path.read_text(encoding="utf-8")
        self.assertIn("\"auth\"", saved_text)
        self.assertIn("\"user_settings\"", saved_text)
        self.assertIn("https://obsync.example.com", saved_text)
        self.assertIn("new-fns-token", saved_text)
        config_data = config_response.json()
        self.assertTrue(config_data["fns_enabled"])
        self.assertEqual(config_data["fns_base_url"], "https://obsync.example.com")

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


if __name__ == "__main__":
    unittest.main()
