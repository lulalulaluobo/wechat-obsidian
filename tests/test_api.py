import io
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
        self.client = TestClient(app)

    def test_get_config(self):
        response = self.client.get("/api/config")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("default_output_dir", data)
        self.assertIn("r2_config_exists", data)

    def test_convert_success(self):
        fake_result = {"title": "示例", "markdown_file": r"D:\obsidian\00_Inbox\01_示例\示例.md"}
        with patch("app.api.routes.run_pipeline", return_value=fake_result):
            response = self.client.post("/api/convert", json={"url": "https://mp.weixin.qq.com/s/example"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        self.assertEqual(response.json()["result"]["title"], "示例")

    def test_convert_defaults_to_fns_when_configured(self):
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
            env = {
                "WECHAT_MD_FNS_BASE_URL": "https://fns.example.com",
                "WECHAT_MD_FNS_TOKEN": "fns-token",
                "WECHAT_MD_FNS_VAULT": "MainVault",
            }
            with patch.dict(os.environ, env, clear=False):
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

    def test_api_requires_access_token_when_configured(self):
        with patch.dict(os.environ, {"WECHAT_MD_ACCESS_TOKEN": "secret-token"}, clear=False):
            unauthorized = self.client.get("/api/config")
            authorized = self.client.get(
                "/api/config",
                headers={"Authorization": "Bearer secret-token"},
            )

        self.assertEqual(unauthorized.status_code, 401)
        self.assertEqual(authorized.status_code, 200)

    def test_settings_page_contains_fns_import_actions(self):
        response = self.client.get("/settings")

        self.assertEqual(response.status_code, 200)
        text = response.text
        self.assertIn("从剪贴板导入 FNS", text)
        self.assertIn("解析并填充", text)
        self.assertIn('id="fns-json-input"', text)

    def test_admin_settings_masks_secret_values(self):
        env = {
            "WECHAT_MD_ACCESS_TOKEN": "secret-token",
            "WECHAT_MD_FNS_BASE_URL": "https://fns.example.com",
            "WECHAT_MD_FNS_TOKEN": "fns-secret-token",
            "WECHAT_MD_FNS_VAULT": "MainVault",
        }
        with patch.dict(os.environ, env, clear=False):
            response = self.client.get(
                "/api/admin/settings",
                headers={"Authorization": "Bearer secret-token"},
            )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["fns_base_url"], "https://fns.example.com")
        self.assertTrue(data["fns_token_configured"])
        self.assertNotIn("fns-secret-token", str(data))
        self.assertTrue(data["access_token_configured"])

    def test_admin_settings_put_updates_runtime_config_and_config_endpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_path = Path(temp_dir) / "runtime-config.json"
            env = {
                "WECHAT_MD_RUNTIME_CONFIG_PATH": str(runtime_path),
                "WECHAT_MD_ACCESS_TOKEN": "secret-token",
            }
            with patch.dict(os.environ, env, clear=False):
                save_response = self.client.put(
                    "/api/admin/settings",
                    headers={"Authorization": "Bearer secret-token"},
                    json={
                        "fns_base_url": "https://obsync.example.com",
                        "fns_token": "new-fns-token",
                        "fns_vault": "obsidian",
                        "fns_target_dir": "00_Inbox/微信公众号",
                    },
                )
                config_response = self.client.get(
                    "/api/config",
                    headers={"Authorization": "Bearer secret-token"},
                )

            self.assertEqual(save_response.status_code, 200)
            self.assertTrue(runtime_path.exists())
            saved_text = runtime_path.read_text(encoding="utf-8")

        self.assertIn("https://obsync.example.com", saved_text)
        self.assertIn("new-fns-token", saved_text)
        config_data = config_response.json()
        self.assertTrue(config_data["fns_enabled"])
        self.assertEqual(config_data["fns_base_url"], "https://obsync.example.com")


if __name__ == "__main__":
    unittest.main()
