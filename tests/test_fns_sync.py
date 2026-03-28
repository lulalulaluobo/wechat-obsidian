import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings  # noqa: E402
from app.services import build_output_target, sync_markdown_to_fns  # noqa: E402


class FastNoteSyncTests(unittest.TestCase):
    def test_build_output_target_defaults_to_fns_when_configured(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_path = Path(temp_dir) / "runtime-config.json"
            with patch.dict(
                os.environ,
                {
                    "WECHAT_MD_RUNTIME_CONFIG_PATH": str(runtime_path),
                    "WECHAT_MD_FNS_BASE_URL": "https://fns.example.com",
                    "WECHAT_MD_FNS_TOKEN": "fns-token",
                    "WECHAT_MD_FNS_VAULT": "MainVault",
                },
                clear=False,
            ):
                settings = get_settings()

        self.assertEqual(build_output_target(None, settings), "fns")

    def test_sync_markdown_to_fns_posts_expected_payload(self):
        class FakeResponse:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"code": 1, "status": True, "message": "Success", "data": {"path": "00_Inbox/微信公众号/示例.md"}}

        class FakeSession:
            def __init__(self):
                self.called = None

            def post(self, url, headers=None, json=None, timeout=None):
                self.called = {
                    "url": url,
                    "headers": headers,
                    "json": json,
                    "timeout": timeout,
                }
                return FakeResponse()

        with tempfile.TemporaryDirectory() as temp_dir:
            markdown_path = Path(temp_dir) / "示例.md"
            markdown_path.write_text("# 示例\n\n正文", encoding="utf-8")
            session = FakeSession()
            runtime_path = Path(temp_dir) / "runtime-config.json"
            with patch.dict(
                os.environ,
                {
                    "WECHAT_MD_RUNTIME_CONFIG_PATH": str(runtime_path),
                    "WECHAT_MD_FNS_BASE_URL": "https://fns.example.com",
                    "WECHAT_MD_FNS_TOKEN": "fns-token",
                    "WECHAT_MD_FNS_VAULT": "MainVault",
                    "WECHAT_MD_FNS_TARGET_DIR": "00_Inbox/微信公众号",
                },
                clear=False,
            ):
                result = sync_markdown_to_fns(
                    markdown_path=markdown_path,
                    note_title="示例",
                    folder_name="01_示例",
                    http_session=session,
                )

        self.assertEqual(result["status"], "success")
        self.assertEqual(result["path"], "00_Inbox/微信公众号/示例.md")
        self.assertEqual(session.called["url"], "https://fns.example.com/api/note")
        self.assertEqual(session.called["headers"]["token"], "fns-token")
        self.assertEqual(session.called["json"]["vault"], "MainVault")
        self.assertEqual(session.called["json"]["path"], "00_Inbox/微信公众号/示例.md")
        self.assertIn("# 示例", session.called["json"]["content"])


if __name__ == "__main__":
    unittest.main()
