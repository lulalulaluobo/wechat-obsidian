import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.config import get_settings, load_runtime_config  # noqa: E402


class SettingsTests(unittest.TestCase):
    def test_runtime_config_migrates_flat_fields_and_initializes_admin_user(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_path = Path(temp_dir) / "runtime-config.json"
            runtime_path.write_text(
                '{"fns_base_url":"https://runtime.example.com","fns_token":"runtime-token","fns_vault":"runtime-vault"}',
                encoding="utf-8",
            )
            env = {"WECHAT_MD_RUNTIME_CONFIG_PATH": str(runtime_path)}
            with patch.dict(os.environ, env, clear=False):
                settings = get_settings()
                runtime_data = load_runtime_config(runtime_path)

        self.assertEqual(settings.fns_base_url, "https://runtime.example.com")
        self.assertEqual(settings.fns_token, "runtime-token")
        self.assertEqual(settings.fns_vault, "runtime-vault")
        self.assertEqual(runtime_data["auth"]["user"]["username"], "admin")
        self.assertIn("password_hash", runtime_data["auth"]["user"])
        self.assertIn("user_settings", runtime_data)

    def test_password_hash_is_not_plaintext_admin(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_path = Path(temp_dir) / "runtime-config.json"
            env = {"WECHAT_MD_RUNTIME_CONFIG_PATH": str(runtime_path)}
            with patch.dict(os.environ, env, clear=False):
                runtime_data = load_runtime_config(runtime_path)

        password_hash = runtime_data["auth"]["user"]["password_hash"]
        self.assertNotEqual(password_hash, "admin")
        self.assertIn("$", password_hash)


if __name__ == "__main__":
    unittest.main()
