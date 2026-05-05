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
from app.services import get_sync_store  # noqa: E402


class V52FeatureTests(unittest.TestCase):
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
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    def _login(self, username: str = "admin", password: str = "admin") -> tuple[str, dict]:
        response = self.client.post(
            "/api/session",
            json={"username": username, "password": password},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        csrf_token = payload.get("csrf_token") or ""
        self.assertTrue(csrf_token)
        return csrf_token, payload

    def test_admin_users_api_requires_csrf_and_creates_db_user(self):
        csrf_token, _ = self._login()

        forbidden = self.client.post(
            "/api/admin/users",
            json={"username": "editor", "password": "secret-1", "display_name": "编辑者", "role": "operator"},
        )
        self.assertEqual(forbidden.status_code, 403)

        created = self.client.post(
            "/api/admin/users",
            headers={"X-CSRF-Token": csrf_token},
            json={"username": "editor", "password": "secret-1", "display_name": "编辑者", "role": "operator"},
        )

        self.assertEqual(created.status_code, 200)
        created_payload = created.json()
        self.assertEqual(created_payload["user"]["username"], "editor")

        listing = self.client.get("/api/admin/users")

        self.assertEqual(listing.status_code, 200)
        usernames = {item["username"] for item in listing.json()["items"]}
        self.assertIn("admin", usernames)
        self.assertIn("editor", usernames)

    def test_disabled_user_session_is_rejected(self):
        admin_csrf, _ = self._login()
        created = self.client.post(
            "/api/admin/users",
            headers={"X-CSRF-Token": admin_csrf},
            json={"username": "operator-a", "password": "secret-2", "display_name": "操作员", "role": "operator"},
        )
        self.assertEqual(created.status_code, 200)
        user_id = created.json()["user"]["id"]

        operator_client = TestClient(app)
        operator_login = operator_client.post(
            "/api/session",
            json={"username": "operator-a", "password": "secret-2"},
        )
        self.assertEqual(operator_login.status_code, 200)

        disabled = self.client.put(
            f"/api/admin/users/{user_id}",
            headers={"X-CSRF-Token": admin_csrf},
            json={"status": "disabled"},
        )
        self.assertEqual(disabled.status_code, 200)

        operator_config = operator_client.get("/api/config")
        self.assertEqual(operator_config.status_code, 401)

    def test_bulk_delete_articles_by_filtered_selection_cleans_db_records(self):
        csrf_token, _ = self._login()
        store = get_sync_store()
        article_a, _ = store.upsert_article(
            {
                "article_url": "https://mp.weixin.qq.com/s/delete-a",
                "source_type": "wechat",
                "account_fakeid": "fakeid-1",
                "account_name": "测试号",
                "title": "待删 A",
                "fetch_status": "success",
                "process_status": "success",
            }
        )
        store.create_article_execution(
            article_id=str(article_a["id"]),
            article_url=str(article_a["article_url"]),
            trigger_channel="web",
            source_type="wechat",
            status="success",
        )
        store.record_artifact(str(article_a["article_url"]), "markdown", "/tmp/delete-a.md")
        article_b, _ = store.upsert_article(
            {
                "article_url": "https://mp.weixin.qq.com/s/delete-b",
                "source_type": "wechat",
                "account_fakeid": "fakeid-2",
                "account_name": "保留号",
                "title": "保留 B",
                "fetch_status": "success",
                "process_status": "success",
            }
        )

        response = self.client.post(
            "/api/sync/articles/delete",
            headers={"X-CSRF-Token": csrf_token},
            json={"selection": {"mode": "filtered", "filters": {"account_fakeid": "fakeid-1"}}},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(store.get_article_by_id(str(article_a["id"])))
        self.assertIsNotNone(store.get_article_by_id(str(article_b["id"])))
        self.assertEqual(store.list_article_executions(article_id=str(article_a["id"]))["total"], 0)
        self.assertEqual(len(store.list_artifacts(str(article_a["article_url"]))), 0)

    def test_scheduler_config_defaults_disabled_and_persists(self):
        csrf_token, _ = self._login()

        initial = self.client.get("/api/admin/schedules")
        self.assertEqual(initial.status_code, 200)
        self.assertFalse(initial.json()["source_sync_schedule"]["enabled"])
        self.assertFalse(initial.json()["article_ingest_schedule"]["enabled"])

        updated = self.client.put(
            "/api/admin/schedules",
            headers={"X-CSRF-Token": csrf_token},
            json={
                "source_sync_schedule": {"enabled": True, "frequency": "daily", "time_of_day": "09:30"},
                "article_ingest_schedule": {"enabled": True, "frequency": "weekly", "day_of_week": 1, "time_of_day": "22:15"},
            },
        )

        self.assertEqual(updated.status_code, 200)
        reread = self.client.get("/api/admin/schedules")
        payload = reread.json()
        self.assertTrue(payload["source_sync_schedule"]["enabled"])
        self.assertEqual(payload["source_sync_schedule"]["time_of_day"], "09:30")
        self.assertEqual(payload["article_ingest_schedule"]["frequency"], "weekly")
        self.assertEqual(payload["article_ingest_schedule"]["day_of_week"], 1)

    def test_qr_login_confirm_persists_wechat_credentials(self):
        csrf_token, _ = self._login()
        start_payload = {
            "session_id": "qr-session-1",
            "status": "pending",
            "qrcode_url": "https://mp.weixin.qq.com/cgi-bin/scanloginqrcode?action=getqrcode",
            "expires_in": 300,
        }
        confirm_payload = {
            "session_id": "qr-session-1",
            "status": "confirmed",
            "token": "mp-token-1",
            "cookie": "ua=1; bizuin=2",
            "message": "登录成功",
        }

        with patch("app.api.routes.start_wechat_mp_qr_login", return_value=start_payload):
            started = self.client.post(
                "/api/sync/login/qr/start",
                headers={"X-CSRF-Token": csrf_token},
            )
        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.json()["session_id"], "qr-session-1")

        with patch("app.api.routes.confirm_wechat_mp_qr_login", return_value=confirm_payload):
            confirmed = self.client.post(
                "/api/sync/login/qr/qr-session-1/confirm",
                headers={"X-CSRF-Token": csrf_token},
            )

        self.assertEqual(confirmed.status_code, 200)
        config = self.client.get("/api/sync/config")
        self.assertEqual(config.status_code, 200)
        self.assertTrue(config.json()["wechat_mp_configured"])

    def test_settings_page_uses_clear_ai_wording(self):
        self._login()
        response = self.client.get("/settings")

        self.assertEqual(response.status_code, 200)
        text = response.text
        self.assertIn("启用 AI 摘要与 Frontmatter", text)
        self.assertIn("启用正文补充块生成", text)
        self.assertIn("启用全文正文润色", text)
        self.assertNotIn("允许生成额外正文补充块", text)

    def test_article_execution_records_bot_receive_metadata(self):
        store = get_sync_store()
        article, _ = store.upsert_article(
            {
                "article_url": "https://mp.weixin.qq.com/s/bot-meta",
                "source_type": "wechat",
                "fetch_status": "queued",
                "process_status": "queued",
            }
        )

        execution = store.create_article_execution(
            article_id=str(article["id"]),
            article_url=str(article["article_url"]),
            trigger_channel="telegram",
            source_type="wechat",
            receive_mode="polling",
            bot_sender_id="123456",
            bot_chat_id="123456",
            bot_message_id="778",
            deployment_mode="nas",
        )

        fetched = store.get_article_execution(str(execution["id"]))
        self.assertEqual(fetched["receive_mode"], "polling")
        self.assertEqual(fetched["bot_sender_id"], "123456")
        self.assertEqual(fetched["bot_chat_id"], "123456")
        self.assertEqual(fetched["bot_message_id"], "778")
        self.assertEqual(fetched["deployment_mode"], "nas")


if __name__ == "__main__":
    unittest.main()
