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


class SettingsTests(unittest.TestCase):
    def test_runtime_config_overrides_environment_defaults(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_path = Path(temp_dir) / "runtime-config.json"
            runtime_path.write_text(
                '{"fns_base_url":"https://runtime.example.com","fns_token":"runtime-token","fns_vault":"runtime-vault"}',
                encoding="utf-8",
            )
            env = {
                "WECHAT_MD_RUNTIME_CONFIG_PATH": str(runtime_path),
                "WECHAT_MD_FNS_BASE_URL": "https://env.example.com",
                "WECHAT_MD_FNS_TOKEN": "env-token",
                "WECHAT_MD_FNS_VAULT": "env-vault",
            }
            with patch.dict(os.environ, env, clear=False):
                settings = get_settings()

        self.assertEqual(settings.fns_base_url, "https://runtime.example.com")
        self.assertEqual(settings.fns_token, "runtime-token")
        self.assertEqual(settings.fns_vault, "runtime-vault")


if __name__ == "__main__":
    unittest.main()
