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

from app.auth import verify_password  # noqa: E402
from app.config import get_settings, load_runtime_config, reset_admin_credentials, save_runtime_config  # noqa: E402


class SettingsTests(unittest.TestCase):
    def test_runtime_config_migrates_flat_fields_and_initializes_admin_user(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_path = Path(temp_dir) / "runtime-config.json"
            runtime_path.write_text(
                '{"fns_base_url":"https://runtime.example.com","fns_token":"runtime-token","fns_vault":"runtime-vault"}',
                encoding="utf-8",
            )
            env = {
                "WECHAT_MD_RUNTIME_CONFIG_PATH": str(runtime_path),
                "WECHAT_MD_APP_MASTER_KEY": "test-master-key",
                "WECHAT_MD_ADMIN_PASSWORD": "admin",
            }
            with patch.dict(os.environ, env, clear=False):
                settings = get_settings()
                runtime_data = load_runtime_config(runtime_path)

        self.assertEqual(settings.fns_base_url, "https://runtime.example.com")
        self.assertEqual(settings.fns_token, "runtime-token")
        self.assertEqual(settings.fns_vault, "runtime-vault")
        self.assertEqual(runtime_data["auth"]["user"]["username"], "admin")
        self.assertIn("password_hash", runtime_data["auth"]["user"])
        self.assertIn("user_settings", runtime_data)
        self.assertEqual(runtime_data["user_settings"]["image_mode"], "wechat_hotlink")
        self.assertIn("image_storage", runtime_data["user_settings"])

    def test_password_hash_is_not_plaintext_admin(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_path = Path(temp_dir) / "runtime-config.json"
            env = {
                "WECHAT_MD_RUNTIME_CONFIG_PATH": str(runtime_path),
                "WECHAT_MD_APP_MASTER_KEY": "test-master-key",
            }
            with patch.dict(os.environ, env, clear=False):
                runtime_data = load_runtime_config(runtime_path)

        password_hash = runtime_data["auth"]["user"]["password_hash"]
        self.assertNotEqual(password_hash, "admin")
        self.assertIn("$", password_hash)
        self.assertFalse(verify_password("admin", password_hash))

    def test_save_runtime_config_persists_s3_image_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_path = Path(temp_dir) / "runtime-config.json"
            env = {
                "WECHAT_MD_RUNTIME_CONFIG_PATH": str(runtime_path),
                "WECHAT_MD_APP_MASTER_KEY": "test-master-key",
                "WECHAT_MD_ADMIN_PASSWORD": "admin",
            }
            with patch.dict(os.environ, env, clear=False):
                save_runtime_config(
                    {
                        "image_mode": "s3_hotlink",
                        "image_storage_endpoint": "https://s3.example.com",
                        "image_storage_region": "auto",
                        "image_storage_bucket": "bucket-a",
                        "image_storage_access_key_id": "key-1",
                        "image_storage_secret_access_key": "secret-1",
                        "image_storage_path_template": "wechat/{year}/{filename}",
                        "image_storage_public_base_url": "https://img.example.com",
                    }
                )
                settings = get_settings()
                runtime_data = load_runtime_config(runtime_path)
                runtime_text = runtime_path.read_text(encoding="utf-8")

        self.assertEqual(settings.image_mode, "s3_hotlink")
        self.assertEqual(settings.image_storage_endpoint, "https://s3.example.com")
        self.assertEqual(settings.image_storage_public_base_url, "https://img.example.com")
        self.assertEqual(runtime_data["user_settings"]["image_mode"], "s3_hotlink")
        self.assertEqual(runtime_data["user_settings"]["image_storage"]["bucket"], "bucket-a")
        self.assertNotIn("secret-1", runtime_text)

    def test_runtime_config_uses_env_admin_password_on_first_init(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_path = Path(temp_dir) / "runtime-config.json"
            env = {
                "WECHAT_MD_RUNTIME_CONFIG_PATH": str(runtime_path),
                "WECHAT_MD_APP_MASTER_KEY": "test-master-key",
                "WECHAT_MD_ADMIN_USERNAME": "rooter",
                "WECHAT_MD_ADMIN_PASSWORD": "super-secret",
            }
            with patch.dict(os.environ, env, clear=False):
                settings = get_settings()
                runtime_data = load_runtime_config(runtime_path)

        self.assertEqual(settings.username, "rooter")
        self.assertTrue(verify_password("super-secret", settings.password_hash))
        self.assertEqual(runtime_data["auth"]["user"]["username"], "rooter")

    def test_runtime_config_encrypts_sensitive_fields(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_path = Path(temp_dir) / "runtime-config.json"
            env = {
                "WECHAT_MD_RUNTIME_CONFIG_PATH": str(runtime_path),
                "WECHAT_MD_APP_MASTER_KEY": "test-master-key",
                "WECHAT_MD_ADMIN_PASSWORD": "admin",
            }
            with patch.dict(os.environ, env, clear=False):
                save_runtime_config(
                    {
                        "fns_base_url": "https://runtime.example.com",
                        "fns_token": "runtime-token",
                        "fns_vault": "runtime-vault",
                        "image_mode": "s3_hotlink",
                        "image_storage_endpoint": "https://s3.example.com",
                        "image_storage_region": "auto",
                        "image_storage_bucket": "bucket-a",
                        "image_storage_access_key_id": "key-1",
                        "image_storage_secret_access_key": "secret-1",
                        "image_storage_path_template": "wechat/{year}/{filename}",
                        "image_storage_public_base_url": "https://img.example.com",
                    }
                )
                runtime_text = runtime_path.read_text(encoding="utf-8")
                settings = get_settings()

        self.assertNotIn("runtime-token", runtime_text)
        self.assertNotIn("secret-1", runtime_text)
        self.assertIn("fns_token_encrypted", runtime_text)
        self.assertIn("secret_access_key_encrypted", runtime_text)
        self.assertTrue(settings.fns_enabled)

    def test_save_runtime_config_persists_ai_settings_and_encrypts_api_key(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_path = Path(temp_dir) / "runtime-config.json"
            env = {
                "WECHAT_MD_RUNTIME_CONFIG_PATH": str(runtime_path),
                "WECHAT_MD_APP_MASTER_KEY": "test-master-key",
                "WECHAT_MD_ADMIN_PASSWORD": "admin",
            }
            with patch.dict(os.environ, env, clear=False):
                save_runtime_config(
                    {
                        "ai_enabled": True,
                        "ai_base_url": "https://api.example.com/v1",
                        "ai_api_key": "ai-key-1",
                        "ai_model": "gpt-5.4-mini",
                        "ai_prompt_template": "请总结 {{title}}",
                        "ai_frontmatter_template": "---\ntitle: {{title}}\nsummary: {{summary}}\n---",
                        "ai_body_template": "> [!summary]\n> {{summary}}",
                        "ai_context_template": "{{title}}\n\n{{content}}",
                        "ai_template_source": "clipper_import",
                        "ai_allow_body_polish": True,
                        "ai_enable_content_polish": True,
                        "ai_content_polish_prompt": "请把正文整理为更适合 Obsidian 阅读的 Markdown",
                    }
                )
                settings = get_settings()
                runtime_text = runtime_path.read_text(encoding="utf-8")
                runtime_data = load_runtime_config(runtime_path)

        self.assertTrue(settings.ai_enabled)
        self.assertEqual(settings.ai_base_url, "https://api.example.com/v1")
        self.assertEqual(settings.ai_model, "gpt-5.4-mini")
        self.assertTrue(settings.ai_allow_body_polish)
        self.assertTrue(settings.ai_enable_content_polish)
        self.assertEqual(settings.ai_context_template, "{{title}}\n\n{{content}}")
        self.assertEqual(settings.ai_template_source, "clipper_import")
        self.assertEqual(settings.ai_content_polish_prompt, "请把正文整理为更适合 Obsidian 阅读的 Markdown")
        self.assertEqual(runtime_data["user_settings"]["ai"]["selected_model_id"], "model-openai-compatible-default")
        self.assertEqual(runtime_data["user_settings"]["ai"]["models"][0]["model_id"], "gpt-5.4-mini")
        self.assertNotIn("ai-key-1", runtime_text)
        self.assertIn("api_key_encrypted", runtime_text)

    def test_runtime_config_migrates_legacy_ai_fields_into_provider_model_registry(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_path = Path(temp_dir) / "runtime-config.json"
            runtime_path.write_text(
                json.dumps(
                    {
                        "auth": {
                            "user": {
                                "username": "admin",
                                "password_hash": "placeholder",
                            },
                            "session_secret": "session-secret",
                        },
                        "user_settings": {
                            "ai_enabled": True,
                            "ai_base_url": "https://api.example.com/v1",
                            "ai_api_key": "legacy-ai-key",
                            "ai_model": "gpt-5.4-mini",
                            "ai_prompt_template": "请总结 {{title}}",
                            "ai_frontmatter_template": "---\ntitle: {{title}}\n---",
                            "ai_body_template": "{{content}}",
                            "ai_context_template": "{{content}}",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            env = {
                "WECHAT_MD_RUNTIME_CONFIG_PATH": str(runtime_path),
                "WECHAT_MD_APP_MASTER_KEY": "test-master-key",
                "WECHAT_MD_ADMIN_PASSWORD": "admin",
            }
            with patch.dict(os.environ, env, clear=False):
                runtime_data = load_runtime_config(runtime_path)
                settings = get_settings()

        self.assertIn("ai", runtime_data["user_settings"])
        self.assertGreaterEqual(len(runtime_data["user_settings"]["ai"]["providers"]), 5)
        self.assertEqual(settings.ai_selected_provider["type"], "openai_compatible")
        self.assertEqual(settings.ai_selected_provider["base_url"], "https://api.example.com/v1")
        self.assertEqual(settings.ai_selected_provider["api_key"], "legacy-ai-key")
        self.assertEqual(settings.ai_selected_model["model_id"], "gpt-5.4-mini")
        self.assertEqual(settings.ai_selected_model_id, settings.ai_selected_model["id"])
        self.assertEqual(settings.ai_model, "gpt-5.4-mini")

    def test_save_runtime_config_persists_ai_registry_and_encrypts_provider_api_keys(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_path = Path(temp_dir) / "runtime-config.json"
            env = {
                "WECHAT_MD_RUNTIME_CONFIG_PATH": str(runtime_path),
                "WECHAT_MD_APP_MASTER_KEY": "test-master-key",
                "WECHAT_MD_ADMIN_PASSWORD": "admin",
            }
            with patch.dict(os.environ, env, clear=False):
                save_runtime_config(
                    {
                        "ai_enabled": True,
                        "ai_providers": [
                            {
                                "id": "openai-compatible-default",
                                "type": "openai_compatible",
                                "display_name": "OpenAI Compatible",
                                "built_in": True,
                                "enabled": True,
                                "base_url": "https://xinyuanai666.com/v1",
                                "api_key": "provider-secret",
                            },
                            {
                                "id": "anthropic-default",
                                "type": "anthropic",
                                "display_name": "Anthropic",
                                "built_in": True,
                                "enabled": False,
                                "base_url": "",
                                "api_key": "",
                            },
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
                        "ai_prompt_template": '{"summary":"一句话总结"}',
                        "ai_frontmatter_template": "---\ntitle: {{title}}\nsummary: {{summary}}\n---",
                        "ai_body_template": "{{content}}",
                        "ai_context_template": "{{content}}",
                    }
                )
                settings = get_settings()
                runtime_text = runtime_path.read_text(encoding="utf-8")
                runtime_data = load_runtime_config(runtime_path)

        self.assertTrue(settings.ai_enabled)
        self.assertEqual(settings.ai_selected_provider["type"], "openai_compatible")
        self.assertEqual(settings.ai_selected_provider["base_url"], "https://xinyuanai666.com/v1")
        self.assertEqual(settings.ai_selected_model["model_id"], "gpt-5.4-mini")
        self.assertEqual(runtime_data["user_settings"]["ai"]["selected_model_id"], "model-openai-compatible-gpt54mini")
        self.assertNotIn("provider-secret", runtime_text)
        self.assertIn("api_key_encrypted", runtime_text)

    def test_runtime_config_requires_correct_master_key_for_encrypted_secrets(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_path = Path(temp_dir) / "runtime-config.json"
            with patch.dict(
                os.environ,
                {
                    "WECHAT_MD_RUNTIME_CONFIG_PATH": str(runtime_path),
                    "WECHAT_MD_APP_MASTER_KEY": "correct-master-key",
                    "WECHAT_MD_ADMIN_PASSWORD": "admin",
                },
                clear=False,
            ):
                save_runtime_config(
                    {
                        "fns_base_url": "https://runtime.example.com",
                        "fns_token": "runtime-token",
                        "fns_vault": "runtime-vault",
                    }
                )

            with patch.dict(
                os.environ,
                {
                    "WECHAT_MD_RUNTIME_CONFIG_PATH": str(runtime_path),
                    "WECHAT_MD_APP_MASTER_KEY": "wrong-master-key",
                    "WECHAT_MD_ADMIN_PASSWORD": "admin",
                },
                clear=False,
            ):
                with self.assertRaises(RuntimeError):
                    get_settings()

    def test_reset_admin_credentials_updates_password_and_session_secret(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            runtime_path = Path(temp_dir) / "runtime-config.json"
            env = {
                "WECHAT_MD_RUNTIME_CONFIG_PATH": str(runtime_path),
                "WECHAT_MD_APP_MASTER_KEY": "test-master-key",
                "WECHAT_MD_ADMIN_USERNAME": "admin",
                "WECHAT_MD_ADMIN_PASSWORD": "admin",
            }
            with patch.dict(os.environ, env, clear=False):
                before = load_runtime_config(runtime_path)
                updated = reset_admin_credentials(username="rooter", new_password="new-secret")
                settings = get_settings()

        self.assertEqual(updated["auth"]["user"]["username"], "rooter")
        self.assertEqual(settings.username, "rooter")
        self.assertTrue(verify_password("new-secret", settings.password_hash))
        self.assertNotEqual(before["auth"]["session_secret"], updated["auth"]["session_secret"])


if __name__ == "__main__":
    unittest.main()
