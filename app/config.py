from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.auth import (
    build_initial_admin_credentials,
    decrypt_secret,
    emit_generated_admin_password,
    encrypt_secret,
    generate_session_secret,
    hash_password,
    session_cookie_secure_enabled,
)


DEFAULT_FNS_TARGET_DIR = "00_Inbox/微信公众号"
DEFAULT_IMAGE_MODE = "wechat_hotlink"
DEFAULT_SINGLE_CONVERSION_HARD_TIMEOUT_SECONDS = 180
IMAGE_MODE_VALUES = {"wechat_hotlink", "s3_hotlink"}
DEFAULT_TELEGRAM_NOTIFY_ON_COMPLETE = True
DEFAULT_FEISHU_NOTIFY_ON_COMPLETE = True
FEISHU_WEBHOOK_PATH = "/api/integrations/feishu/webhook"
DEFAULT_AI_MODEL = "gpt-5.4-mini"
DEFAULT_AI_PROMPT_TEMPLATE = """你是一个 Obsidian 笔记解释器。请基于提供的标题、作者、原文链接和清洗后的 Markdown 正文，提炼结构化笔记变量。

请只返回 JSON 对象，不要输出 Markdown，不要额外解释。JSON 字段固定为：
- summary: 一句话总结，说明这篇文章解决什么问题或传达什么核心观点
- tags: 3 到 5 个中文或英文 tag，使用数组返回，每个 tag 不要包含空格
- my_understand: 2 到 4 句话，说明阅读后的理解、适用场景或个人启发
- body_polish: 可选的补充块内容。如果没有额外补充，返回空字符串

上下文：
- 标题：{{title}}
- 作者：{{author}}
- 原文链接：{{url}}
- 日期：{{date}}

正文：
{{content}}
"""
DEFAULT_AI_FRONTMATTER_TEMPLATE = """---
title: {{title}}
author: {{author}}
source: {{url}}
created_day: {{date}}
summary: {{summary}}
tags: {{tags}}
---
"""
DEFAULT_AI_BODY_TEMPLATE = """> [!summary] 一句话总结
> {{summary}}

---

> [!tip] 我的理解
> {{my_understand}}

{{body_polish}}
"""
DEFAULT_AI_CONTEXT_TEMPLATE = "{{content}}"
DEFAULT_AI_CONTENT_POLISH_PROMPT = """请把正文整理为更适合 Obsidian 阅读的 Markdown。

要求：
1. 不改变原文事实、观点和结论
2. 保留所有图片、链接、代码块、表格和列表
3. 代码块必须使用三个反引号 fenced code block
4. 表格必须输出为标准 Markdown 表格
5. 适度优化标题层级、空行、列表结构和段落组织，提升阅读体验
6. 不要输出解释，只返回润色后的正文 Markdown
"""
AI_TEMPLATE_SOURCE_VALUES = {"manual", "clipper_import"}
AI_PROVIDER_TYPE_VALUES = {"openai_compatible", "anthropic", "gemini", "ollama", "openrouter", "custom"}
BUILTIN_AI_PROVIDER_DEFINITIONS = (
    {
        "id": "openai-compatible-default",
        "type": "openai_compatible",
        "display_name": "OpenAI Compatible",
        "built_in": True,
        "enabled": True,
        "base_url": "",
        "api_key": "",
    },
    {
        "id": "anthropic-default",
        "type": "anthropic",
        "display_name": "Anthropic",
        "built_in": True,
        "enabled": True,
        "base_url": "https://api.anthropic.com/v1",
        "api_key": "",
    },
    {
        "id": "gemini-default",
        "type": "gemini",
        "display_name": "Gemini",
        "built_in": True,
        "enabled": True,
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "api_key": "",
    },
    {
        "id": "ollama-default",
        "type": "ollama",
        "display_name": "Ollama",
        "built_in": True,
        "enabled": True,
        "base_url": "http://127.0.0.1:11434",
        "api_key": "",
    },
    {
        "id": "openrouter-default",
        "type": "openrouter",
        "display_name": "OpenRouter",
        "built_in": True,
        "enabled": True,
        "base_url": "https://openrouter.ai/api/v1",
        "api_key": "",
    },
)


@dataclass(frozen=True)
class Settings:
    default_output_dir: Path
    runtime_config_path: Path
    username: str
    password_hash: str
    session_secret: str
    session_cookie_secure: bool
    default_timeout: int = 30
    fns_base_url: str | None = None
    fns_token: str | None = None
    fns_vault: str | None = None
    fns_target_dir: str = DEFAULT_FNS_TARGET_DIR
    cleanup_temp_on_success: bool = True
    single_conversion_isolation_enabled: bool = True
    single_conversion_hard_timeout_seconds: int = DEFAULT_SINGLE_CONVERSION_HARD_TIMEOUT_SECONDS
    image_mode: str = DEFAULT_IMAGE_MODE
    image_storage_provider: str | None = None
    image_storage_endpoint: str | None = None
    image_storage_region: str | None = None
    image_storage_bucket: str | None = None
    image_storage_access_key_id: str | None = None
    image_storage_secret_access_key: str | None = None
    image_storage_path_template: str | None = None
    image_storage_public_base_url: str | None = None
    telegram_enabled: bool = False
    telegram_bot_token: str | None = None
    telegram_webhook_public_base_url: str | None = None
    telegram_webhook_secret: str | None = None
    telegram_allowed_chat_ids: tuple[str, ...] = ()
    telegram_notify_on_complete: bool = DEFAULT_TELEGRAM_NOTIFY_ON_COMPLETE
    telegram_webhook_status: str = "inactive"
    telegram_webhook_message: str = ""
    telegram_image_mode: str | None = None
    feishu_enabled: bool = False
    feishu_app_id: str | None = None
    feishu_app_secret: str | None = None
    feishu_verification_token: str | None = None
    feishu_encrypt_key: str | None = None
    feishu_webhook_public_base_url: str | None = None
    feishu_allowed_open_ids: tuple[str, ...] = ()
    feishu_notify_on_complete: bool = DEFAULT_FEISHU_NOTIFY_ON_COMPLETE
    feishu_webhook_status: str = "inactive"
    feishu_webhook_message: str = ""
    feishu_image_mode: str | None = None
    ai_enabled: bool = False
    ai_providers: tuple[dict[str, Any], ...] = ()
    ai_models: tuple[dict[str, Any], ...] = ()
    ai_selected_model_id: str = ""
    ai_selected_provider: dict[str, Any] | None = None
    ai_selected_model: dict[str, Any] | None = None
    ai_base_url: str | None = None
    ai_api_key: str | None = None
    ai_model: str = DEFAULT_AI_MODEL
    ai_prompt_template: str = DEFAULT_AI_PROMPT_TEMPLATE
    ai_frontmatter_template: str = DEFAULT_AI_FRONTMATTER_TEMPLATE
    ai_body_template: str = DEFAULT_AI_BODY_TEMPLATE
    ai_context_template: str = DEFAULT_AI_CONTEXT_TEMPLATE
    ai_allow_body_polish: bool = False
    ai_enable_content_polish: bool = False
    ai_content_polish_prompt: str = DEFAULT_AI_CONTENT_POLISH_PROMPT
    ai_template_source: str = "manual"
    wechat_mp_token: str | None = None
    wechat_mp_cookie: str | None = None

    @property
    def fns_enabled(self) -> bool:
        return bool(self.fns_base_url and self.fns_token and self.fns_vault)

    @property
    def image_storage_enabled(self) -> bool:
        return self.image_mode == "s3_hotlink" and all(
            [
                self.image_storage_provider == "s3",
                self.image_storage_endpoint,
                self.image_storage_region,
                self.image_storage_bucket,
                self.image_storage_access_key_id,
                self.image_storage_secret_access_key,
                self.image_storage_path_template,
                self.image_storage_public_base_url,
            ]
        )

    @property
    def telegram_enabled_and_configured(self) -> bool:
        return bool(
            self.telegram_enabled
            and self.telegram_bot_token
            and self.telegram_webhook_public_base_url
            and self.telegram_webhook_secret
            and self.telegram_allowed_chat_ids
        )

    @property
    def telegram_webhook_url(self) -> str | None:
        if not self.telegram_webhook_public_base_url:
            return None
        return f"{self.telegram_webhook_public_base_url.rstrip('/')}/api/integrations/telegram/webhook"

    @property
    def feishu_enabled_and_configured(self) -> bool:
        return bool(
            self.feishu_enabled
            and self.feishu_app_id
            and self.feishu_app_secret
            and self.feishu_verification_token
            and self.feishu_webhook_public_base_url
        )

    @property
    def feishu_webhook_url(self) -> str | None:
        if not self.feishu_webhook_public_base_url:
            return None
        normalized_base = _normalize_feishu_webhook_public_base_url(self.feishu_webhook_public_base_url)
        return f"{normalized_base.rstrip('/')}{FEISHU_WEBHOOK_PATH}"

    @property
    def ai_configured(self) -> bool:
        return bool(
            self.ai_selected_provider
            and self.ai_selected_model
            and (
                self.ai_selected_provider.get("type") == "ollama"
                or self.ai_api_key
                or self.ai_selected_provider.get("type") == "openai_compatible"
            )
            and self.ai_base_url
            and self.ai_model
            and self.ai_prompt_template.strip()
            and self.ai_frontmatter_template.strip()
            and self.ai_body_template.strip()
            and self.ai_context_template.strip()
        )

    @property
    def wechat_mp_configured(self) -> bool:
        return bool(self.wechat_mp_token and self.wechat_mp_cookie)


FNS_FIELDS = {
    "fns_base_url",
    "fns_token",
    "fns_vault",
    "fns_target_dir",
    "cleanup_temp_on_success",
}
IMAGE_STORAGE_TEXT_FIELDS = {
    "image_storage_provider",
    "image_storage_endpoint",
    "image_storage_region",
    "image_storage_bucket",
    "image_storage_access_key_id",
    "image_storage_secret_access_key",
    "image_storage_path_template",
    "image_storage_public_base_url",
}
SECRET_FIELDS = {"fns_token", "image_storage_secret_access_key"}
TELEGRAM_BOOL_FIELDS = {"telegram_enabled", "telegram_notify_on_complete"}
TELEGRAM_TEXT_FIELDS = {
    "telegram_webhook_public_base_url",
    "telegram_webhook_status",
    "telegram_webhook_message",
}
TELEGRAM_SECRET_FIELDS = {"telegram_bot_token", "telegram_webhook_secret"}
FEISHU_BOOL_FIELDS = {"feishu_enabled", "feishu_notify_on_complete"}
FEISHU_TEXT_FIELDS = {
    "feishu_app_id",
    "feishu_webhook_public_base_url",
    "feishu_webhook_status",
    "feishu_webhook_message",
}
FEISHU_SECRET_FIELDS = {"feishu_app_secret", "feishu_verification_token", "feishu_encrypt_key"}
SECRET_FIELDS = SECRET_FIELDS | TELEGRAM_SECRET_FIELDS | FEISHU_SECRET_FIELDS
WECHAT_MP_SECRET_FIELDS = {"wechat_mp_token", "wechat_mp_cookie"}
SECRET_FIELDS = SECRET_FIELDS | WECHAT_MP_SECRET_FIELDS
TELEGRAM_TEXT_FIELD_MAP = {
    "telegram_webhook_public_base_url": "webhook_public_base_url",
    "telegram_webhook_status": "webhook_status",
    "telegram_webhook_message": "webhook_message",
}
FEISHU_TEXT_FIELD_MAP = {
    "feishu_app_id": "app_id",
    "feishu_webhook_public_base_url": "webhook_public_base_url",
    "feishu_webhook_status": "webhook_status",
    "feishu_webhook_message": "webhook_message",
}
AI_BOOL_FIELDS = {"ai_enabled", "ai_allow_body_polish", "ai_enable_content_polish"}
AI_TEXT_FIELDS = {
    "ai_prompt_template",
    "ai_frontmatter_template",
    "ai_body_template",
    "ai_context_template",
    "ai_content_polish_prompt",
    "ai_template_source",
}
LEGACY_AI_TEXT_FIELDS = {"ai_base_url", "ai_model"}
LEGACY_AI_SECRET_FIELDS = {"ai_api_key"}
AI_REGISTRY_FIELDS = {"ai_providers", "ai_models", "ai_selected_model_id"}
SECRET_FIELDS = SECRET_FIELDS | LEGACY_AI_SECRET_FIELDS


def get_runtime_config_path() -> Path:
    configured = os.environ.get("WECHAT_MD_RUNTIME_CONFIG_PATH")
    if configured:
        return Path(configured).resolve()
    return (Path(__file__).resolve().parents[1] / "data" / "runtime-config.json").resolve()


def load_runtime_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or get_runtime_config_path()
    if config_path.exists():
        try:
            raw_data = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise RuntimeError(f"运行时配置文件不是有效 JSON: {config_path}") from error
        if not isinstance(raw_data, dict):
            raise RuntimeError(f"运行时配置文件结构无效: {config_path}")
    else:
        raw_data = {}

    normalized = _normalize_runtime_config(raw_data)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    _write_runtime_config(config_path, normalized)
    return normalized


def save_runtime_config(payload: dict[str, Any], clear_fields: list[str] | None = None) -> dict[str, Any]:
    config_path = get_runtime_config_path()
    current = load_runtime_config(config_path)
    updated = _normalize_runtime_config(current)
    user_settings = dict(updated["user_settings"])
    image_storage = dict(user_settings["image_storage"])
    telegram_settings = dict(user_settings["telegram"])
    feishu_settings = dict(user_settings["feishu"])
    wechat_mp_settings = dict(user_settings.get("wechat_mp") or {})
    clear_set = {field for field in (clear_fields or []) if field in SECRET_FIELDS}

    for field in clear_set:
        if field == "fns_token":
            user_settings["fns_token"] = ""
        elif field == "image_storage_secret_access_key":
            image_storage["secret_access_key"] = ""
        elif field == "telegram_bot_token":
            telegram_settings["bot_token"] = ""
        elif field == "telegram_webhook_secret":
            telegram_settings["webhook_secret"] = ""
        elif field == "feishu_app_secret":
            feishu_settings["app_secret"] = ""
        elif field == "feishu_verification_token":
            feishu_settings["verification_token"] = ""
        elif field == "feishu_encrypt_key":
            feishu_settings["encrypt_key"] = ""
        elif field == "wechat_mp_token":
            wechat_mp_settings["token"] = ""
        elif field == "wechat_mp_cookie":
            wechat_mp_settings["cookie"] = ""
        elif field == "ai_api_key":
            ai_block = dict(user_settings.get("ai") or {})
            providers = [dict(item) for item in ai_block.get("providers", []) if isinstance(item, dict)]
            selected_model_id = str(ai_block.get("selected_model_id") or "").strip()
            model_map = {str(item.get("id") or ""): item for item in ai_block.get("models", []) if isinstance(item, dict)}
            selected_model = model_map.get(selected_model_id)
            selected_provider_id = str(selected_model.get("provider_id") or "") if selected_model else ""
            for provider in providers:
                if str(provider.get("id") or "") == selected_provider_id:
                    provider["api_key"] = ""
            user_settings["ai"] = {
                "providers": providers,
                "models": [dict(item) for item in ai_block.get("models", []) if isinstance(item, dict)],
                "selected_model_id": selected_model_id,
            }

    for field in FNS_FIELDS:
        if field not in payload:
            continue
        raw_value = payload.get(field)
        if field == "cleanup_temp_on_success":
            user_settings[field] = _as_bool(raw_value, default=True)
            continue
        if raw_value is None:
            continue
        user_settings[field] = str(raw_value).strip()

    if "single_conversion_isolation_enabled" in payload:
        user_settings["single_conversion_isolation_enabled"] = _as_bool(
            payload.get("single_conversion_isolation_enabled"),
            default=True,
        )
    if "single_conversion_hard_timeout_seconds" in payload and payload.get("single_conversion_hard_timeout_seconds") is not None:
        user_settings["single_conversion_hard_timeout_seconds"] = _as_int(
            payload.get("single_conversion_hard_timeout_seconds"),
            default=DEFAULT_SINGLE_CONVERSION_HARD_TIMEOUT_SECONDS,
            minimum=1,
        )

    if "image_mode" in payload and payload.get("image_mode") is not None:
        user_settings["image_mode"] = str(payload.get("image_mode") or "").strip() or DEFAULT_IMAGE_MODE

    for field in IMAGE_STORAGE_TEXT_FIELDS:
        if field not in payload:
            continue
        raw_value = payload.get(field)
        if raw_value is None:
            continue
        image_storage[field.removeprefix("image_storage_")] = str(raw_value).strip()

    for field in TELEGRAM_BOOL_FIELDS:
        if field not in payload:
            continue
        telegram_settings[field.removeprefix("telegram_")] = _as_bool(payload.get(field), default=field == "telegram_notify_on_complete")

    for field in TELEGRAM_TEXT_FIELDS:
        if field not in payload:
            continue
        raw_value = payload.get(field)
        if raw_value is None:
            continue
        telegram_settings[TELEGRAM_TEXT_FIELD_MAP[field]] = str(raw_value).strip()

    for field in TELEGRAM_SECRET_FIELDS:
        if field not in payload:
            continue
        raw_value = payload.get(field)
        if raw_value is None:
            continue
        telegram_settings[field.removeprefix("telegram_")] = str(raw_value).strip()

    if "telegram_allowed_chat_ids" in payload:
        telegram_settings["allowed_chat_ids"] = _normalize_chat_ids(payload.get("telegram_allowed_chat_ids"))

    if "telegram_image_mode" in payload:
        telegram_settings["image_mode"] = _normalize_entry_image_mode(payload.get("telegram_image_mode"))

    for field in FEISHU_BOOL_FIELDS:
        if field not in payload:
            continue
        feishu_settings[field.removeprefix("feishu_")] = _as_bool(payload.get(field), default=field == "feishu_notify_on_complete")

    for field in FEISHU_TEXT_FIELDS:
        if field not in payload:
            continue
        raw_value = payload.get(field)
        if raw_value is None:
            continue
        normalized_value = str(raw_value).strip()
        if field == "feishu_webhook_public_base_url":
            normalized_value = _normalize_feishu_webhook_public_base_url(normalized_value)
        feishu_settings[FEISHU_TEXT_FIELD_MAP[field]] = normalized_value

    for field in FEISHU_SECRET_FIELDS:
        if field not in payload:
            continue
        raw_value = payload.get(field)
        if raw_value is None:
            continue
        feishu_settings[field.removeprefix("feishu_")] = str(raw_value).strip()

    if "feishu_allowed_open_ids" in payload:
        feishu_settings["allowed_open_ids"] = _normalize_identifier_list(payload.get("feishu_allowed_open_ids"))

    if "feishu_image_mode" in payload:
        feishu_settings["image_mode"] = _normalize_entry_image_mode(payload.get("feishu_image_mode"))

    if "wechat_mp_token" in payload and payload.get("wechat_mp_token") is not None:
        wechat_mp_settings["token"] = str(payload.get("wechat_mp_token") or "").strip()
    if "wechat_mp_cookie" in payload and payload.get("wechat_mp_cookie") is not None:
        wechat_mp_settings["cookie"] = str(payload.get("wechat_mp_cookie") or "").strip()

    for field in AI_BOOL_FIELDS:
        if field not in payload:
            continue
        user_settings[field] = _as_bool(payload.get(field), default=field == "ai_allow_body_polish")

    for field in AI_TEXT_FIELDS:
        if field not in payload:
            continue
        raw_value = payload.get(field)
        if raw_value is None:
            continue
        user_settings[field] = str(raw_value)

    if any(field in payload for field in AI_REGISTRY_FIELDS):
        existing_ai = user_settings.get("ai") if isinstance(user_settings.get("ai"), dict) else {}
        existing_provider_map = {
            str(provider.get("id") or ""): provider
            for provider in existing_ai.get("providers", [])
            if isinstance(provider, dict)
        }
        merged_providers: list[dict[str, Any]] = []
        for provider in payload.get("ai_providers") or []:
            if not isinstance(provider, dict):
                continue
            merged = dict(provider)
            provider_id = str(merged.get("id") or "").strip()
            existing_provider = existing_provider_map.get(provider_id)
            if existing_provider and not str(merged.get("api_key") or "").strip():
                merged["api_key"] = existing_provider.get("api_key") or ""
            merged_providers.append(merged)
        user_settings["ai"] = {
            "providers": merged_providers,
            "models": [item for item in payload.get("ai_models") or [] if isinstance(item, dict)],
            "selected_model_id": str(payload.get("ai_selected_model_id") or "").strip(),
        }
    elif any(field in payload for field in LEGACY_AI_TEXT_FIELDS | LEGACY_AI_SECRET_FIELDS):
        ai_block = dict(user_settings.get("ai") or {})
        providers = [dict(item) for item in ai_block.get("providers", []) if isinstance(item, dict)]
        models = [dict(item) for item in ai_block.get("models", []) if isinstance(item, dict)]
        provider = next((item for item in providers if str(item.get("id")) == "openai-compatible-default"), None)
        if provider is None:
            provider = _build_builtin_ai_providers()[0]
            providers.append(provider)
        if "ai_base_url" in payload and payload.get("ai_base_url") is not None:
            provider["base_url"] = str(payload.get("ai_base_url") or "").strip()
        if "ai_api_key" in payload and payload.get("ai_api_key") is not None:
            provider["api_key"] = str(payload.get("ai_api_key") or "").strip()
        model = next((item for item in models if str(item.get("provider_id")) == "openai-compatible-default"), None)
        if model is None:
            model = {
                "id": "model-openai-compatible-default",
                "provider_id": "openai-compatible-default",
                "display_name": str(payload.get("ai_model") or DEFAULT_AI_MODEL),
                "model_id": str(payload.get("ai_model") or DEFAULT_AI_MODEL),
                "enabled": True,
            }
            models.append(model)
        elif "ai_model" in payload and payload.get("ai_model") is not None:
            model_name = str(payload.get("ai_model") or DEFAULT_AI_MODEL).strip() or DEFAULT_AI_MODEL
            model["display_name"] = model_name
            model["model_id"] = model_name
        user_settings["ai"] = {
            "providers": providers,
            "models": models,
            "selected_model_id": str(ai_block.get("selected_model_id") or model.get("id") or "").strip(),
        }

    user_settings["image_storage"] = image_storage
    user_settings["telegram"] = telegram_settings
    user_settings["feishu"] = feishu_settings
    user_settings["wechat_mp"] = wechat_mp_settings
    updated["user_settings"] = _normalize_user_settings(user_settings)
    _validate_runtime_config(updated)
    _write_runtime_config(config_path, updated)
    return updated


def update_password(current_password: str, new_password: str) -> dict[str, Any]:
    from app.auth import verify_password

    config_path = get_runtime_config_path()
    current = load_runtime_config(config_path)
    auth_user = current["auth"]["user"]
    if not verify_password(current_password, str(auth_user.get("password_hash") or "")):
        raise ValueError("当前密码不正确")

    normalized_password = (new_password or "").strip()
    if not normalized_password:
        raise ValueError("新密码不能为空")

    auth_user["password_hash"] = hash_password(normalized_password)
    current["auth"]["user"] = auth_user
    _write_runtime_config(config_path, current)
    return current


def update_ai_selected_model(selected_model_id: str) -> dict[str, Any]:
    normalized_model_id = str(selected_model_id or "").strip()
    if not normalized_model_id:
        raise ValueError("ai_selected_model_id 不能为空")

    config_path = get_runtime_config_path()
    current = load_runtime_config(config_path)
    ai_block = current["user_settings"].get("ai") if isinstance(current["user_settings"].get("ai"), dict) else {}
    models = [dict(item) for item in ai_block.get("models", []) if isinstance(item, dict)]
    if not any(str(item.get("id") or "") == normalized_model_id for item in models):
        raise ValueError("指定的 AI 模型不存在")

    ai_block["selected_model_id"] = normalized_model_id
    current["user_settings"]["ai"] = ai_block
    _write_runtime_config(config_path, current)
    return current


def reset_admin_credentials(new_password: str, username: str | None = None) -> dict[str, Any]:
    normalized_password = (new_password or "").strip()
    if not normalized_password:
        raise ValueError("新密码不能为空")

    config_path = get_runtime_config_path()
    current = load_runtime_config(config_path)
    auth_user = current["auth"]["user"]
    if username is not None:
        normalized_username = username.strip()
        if not normalized_username:
            raise ValueError("用户名不能为空")
        auth_user["username"] = normalized_username
    auth_user["password_hash"] = hash_password(normalized_password)
    current["auth"]["user"] = auth_user
    current["auth"]["session_secret"] = generate_session_secret()
    _write_runtime_config(config_path, current)
    return current


def update_telegram_webhook_state(status: str, message: str, webhook_url: str | None = None) -> dict[str, Any]:
    config_path = get_runtime_config_path()
    current = load_runtime_config(config_path)
    telegram = dict(current["user_settings"]["telegram"])
    telegram["webhook_status"] = (status or "inactive").strip() or "inactive"
    telegram["webhook_message"] = (message or "").strip()
    if webhook_url is not None:
        telegram["webhook_public_base_url"] = webhook_url.rsplit("/api/integrations/telegram/webhook", 1)[0] if webhook_url else ""
    current["user_settings"]["telegram"] = telegram
    _write_runtime_config(config_path, current)
    return current


def update_feishu_webhook_state(status: str, message: str, webhook_url: str | None = None) -> dict[str, Any]:
    config_path = get_runtime_config_path()
    current = load_runtime_config(config_path)
    feishu = dict(current["user_settings"]["feishu"])
    feishu["webhook_status"] = (status or "inactive").strip() or "inactive"
    feishu["webhook_message"] = (message or "").strip()
    if webhook_url is not None:
        feishu["webhook_public_base_url"] = _normalize_feishu_webhook_public_base_url(webhook_url) if webhook_url else ""
    current["user_settings"]["feishu"] = feishu
    _write_runtime_config(config_path, current)
    return current


def build_admin_settings_payload() -> dict[str, Any]:
    settings = get_settings()
    runtime_values = load_runtime_config(settings.runtime_config_path)
    user_settings = runtime_values["user_settings"]
    image_storage = user_settings["image_storage"]
    telegram = user_settings["telegram"]
    feishu = user_settings["feishu"]
    wechat_mp = user_settings.get("wechat_mp") if isinstance(user_settings.get("wechat_mp"), dict) else {}
    runtime_overrides = [
        "auth.user.username",
        "auth.user.password_hash",
        "auth.session_secret_encrypted",
        *[
            f"user_settings.{key}"
            for key in sorted(user_settings.keys())
            if key not in {"image_storage", "ai"}
        ],
        *[f"user_settings.image_storage.{key}" for key in sorted(image_storage.keys())],
        *[f"user_settings.telegram.{key}" for key in sorted(telegram.keys())],
        *[f"user_settings.feishu.{key}" for key in sorted(feishu.keys())],
        *[f"user_settings.wechat_mp.{key}" for key in sorted(wechat_mp.keys())],
        "user_settings.ai.providers",
        "user_settings.ai.models",
        "user_settings.ai.selected_model_id",
    ]
    return {
        "runtime_config_path": str(settings.runtime_config_path),
        "auth_enabled": True,
        "session_cookie_secure": settings.session_cookie_secure,
        "default_output_target": "fns" if settings.fns_enabled else "local",
        "current_user": {"username": settings.username},
        "fns_base_url": settings.fns_base_url or "",
        "fns_vault": settings.fns_vault or "",
        "fns_target_dir": settings.fns_target_dir or DEFAULT_FNS_TARGET_DIR,
        "fns_token_configured": bool(settings.fns_token),
        "fns_token_masked": _mask_secret(settings.fns_token),
        "cleanup_temp_on_success": settings.cleanup_temp_on_success,
        "single_conversion_isolation_enabled": settings.single_conversion_isolation_enabled,
        "single_conversion_hard_timeout_seconds": settings.single_conversion_hard_timeout_seconds,
        "image_mode": settings.image_mode,
        "image_storage_enabled": settings.image_storage_enabled,
        "image_storage_provider": settings.image_storage_provider or "s3",
        "image_storage_endpoint": settings.image_storage_endpoint or "",
        "image_storage_region": settings.image_storage_region or "",
        "image_storage_bucket": settings.image_storage_bucket or "",
        "image_storage_access_key_id": settings.image_storage_access_key_id or "",
        "image_storage_path_template": settings.image_storage_path_template or "",
        "image_storage_public_base_url": settings.image_storage_public_base_url or "",
        "image_storage_secret_access_key_configured": bool(settings.image_storage_secret_access_key),
        "image_storage_secret_access_key_masked": _mask_secret(settings.image_storage_secret_access_key),
        "telegram_enabled": settings.telegram_enabled,
        "telegram_bot_token_configured": bool(settings.telegram_bot_token),
        "telegram_bot_token_masked": _mask_secret(settings.telegram_bot_token),
        "telegram_webhook_public_base_url": settings.telegram_webhook_public_base_url or "",
        "telegram_webhook_url": settings.telegram_webhook_url or "",
        "telegram_webhook_secret_configured": bool(settings.telegram_webhook_secret),
        "telegram_webhook_secret_masked": _mask_secret(settings.telegram_webhook_secret),
        "telegram_allowed_chat_ids_text": "\n".join(settings.telegram_allowed_chat_ids),
        "telegram_notify_on_complete": settings.telegram_notify_on_complete,
        "telegram_webhook_status": settings.telegram_webhook_status,
        "telegram_webhook_message": settings.telegram_webhook_message,
        "telegram_image_mode": settings.telegram_image_mode or "",
        "feishu_enabled": settings.feishu_enabled,
        "feishu_app_id": settings.feishu_app_id or "",
        "feishu_app_secret_configured": bool(settings.feishu_app_secret),
        "feishu_app_secret_masked": _mask_secret(settings.feishu_app_secret),
        "feishu_verification_token_configured": bool(settings.feishu_verification_token),
        "feishu_verification_token_masked": _mask_secret(settings.feishu_verification_token),
        "feishu_encrypt_key_configured": bool(settings.feishu_encrypt_key),
        "feishu_encrypt_key_masked": _mask_secret(settings.feishu_encrypt_key),
        "feishu_webhook_public_base_url": settings.feishu_webhook_public_base_url or "",
        "feishu_webhook_url": settings.feishu_webhook_url or "",
        "feishu_allowed_open_ids_text": "\n".join(settings.feishu_allowed_open_ids),
        "feishu_notify_on_complete": settings.feishu_notify_on_complete,
        "feishu_webhook_status": settings.feishu_webhook_status,
        "feishu_webhook_message": settings.feishu_webhook_message,
        "feishu_image_mode": settings.feishu_image_mode or "",
        "wechat_mp_configured": settings.wechat_mp_configured,
        "wechat_mp_token_configured": bool(settings.wechat_mp_token),
        "wechat_mp_token_masked": _mask_secret(settings.wechat_mp_token),
        "wechat_mp_cookie_configured": bool(settings.wechat_mp_cookie),
        "wechat_mp_cookie_masked": _mask_secret(settings.wechat_mp_cookie),
        "ai_enabled": settings.ai_enabled,
        "ai_configured": settings.ai_configured,
        "ai_model": settings.ai_model,
        "ai_selected_provider": _sanitize_provider_for_payload(settings.ai_selected_provider),
        "ai_selected_model_id": settings.ai_selected_model_id,
        "ai_providers": [_sanitize_provider_for_payload(item) for item in settings.ai_providers],
        "ai_models": [dict(item) for item in settings.ai_models],
        "ai_prompt_template": settings.ai_prompt_template,
        "ai_frontmatter_template": settings.ai_frontmatter_template,
        "ai_body_template": settings.ai_body_template,
        "ai_context_template": settings.ai_context_template,
        "ai_allow_body_polish": settings.ai_allow_body_polish,
        "ai_enable_content_polish": settings.ai_enable_content_polish,
        "ai_content_polish_prompt": settings.ai_content_polish_prompt,
        "ai_template_source": settings.ai_template_source,
        "runtime_overrides": runtime_overrides,
    }


def get_settings() -> Settings:
    runtime_config_path = get_runtime_config_path()
    runtime_values = load_runtime_config(runtime_config_path)
    auth_block = runtime_values["auth"]
    user_block = auth_block["user"]
    runtime_user_settings = runtime_values["user_settings"]
    image_storage = runtime_user_settings["image_storage"]
    telegram = runtime_user_settings["telegram"]
    feishu = runtime_user_settings["feishu"]
    wechat_mp = runtime_user_settings.get("wechat_mp") if isinstance(runtime_user_settings.get("wechat_mp"), dict) else {}
    ai_registry = runtime_user_settings["ai"]

    output_dir = Path(os.environ.get("WECHAT_MD_DEFAULT_OUTPUT_DIR", r"D:\obsidian\00_Inbox")).resolve()
    fns_base_url = str(
        runtime_user_settings.get("fns_base_url") or os.environ.get("WECHAT_MD_FNS_BASE_URL") or ""
    ).strip() or None
    fns_token = str(
        runtime_user_settings.get("fns_token") or os.environ.get("WECHAT_MD_FNS_TOKEN") or ""
    ).strip() or None
    fns_vault = str(
        runtime_user_settings.get("fns_vault") or os.environ.get("WECHAT_MD_FNS_VAULT") or ""
    ).strip() or None
    fns_target_dir = (
        str(
            runtime_user_settings.get("fns_target_dir")
            or os.environ.get("WECHAT_MD_FNS_TARGET_DIR")
            or DEFAULT_FNS_TARGET_DIR
        ).strip()
        or DEFAULT_FNS_TARGET_DIR
    )
    cleanup_temp_on_success = _as_bool(runtime_user_settings.get("cleanup_temp_on_success"), default=True)
    single_conversion_isolation_enabled = _as_bool(
        os.environ.get("WECHAT_MD_SINGLE_CONVERSION_ISOLATION_ENABLED")
        if os.environ.get("WECHAT_MD_SINGLE_CONVERSION_ISOLATION_ENABLED") is not None
        else runtime_user_settings.get("single_conversion_isolation_enabled"),
        default=True,
    )
    single_conversion_hard_timeout_seconds = _as_int(
        os.environ.get("WECHAT_MD_SINGLE_CONVERSION_HARD_TIMEOUT_SECONDS")
        if os.environ.get("WECHAT_MD_SINGLE_CONVERSION_HARD_TIMEOUT_SECONDS") is not None
        else runtime_user_settings.get("single_conversion_hard_timeout_seconds"),
        default=DEFAULT_SINGLE_CONVERSION_HARD_TIMEOUT_SECONDS,
        minimum=1,
    )
    image_mode = _normalize_image_mode(runtime_user_settings.get("image_mode") or os.environ.get("WECHAT_MD_IMAGE_MODE"))

    provider = str(image_storage.get("provider") or os.environ.get("WECHAT_MD_IMAGE_STORAGE_PROVIDER") or "s3").strip() or "s3"
    endpoint = str(image_storage.get("endpoint") or os.environ.get("WECHAT_MD_IMAGE_STORAGE_ENDPOINT") or "").strip() or None
    region = str(image_storage.get("region") or os.environ.get("WECHAT_MD_IMAGE_STORAGE_REGION") or "").strip() or None
    bucket = str(image_storage.get("bucket") or os.environ.get("WECHAT_MD_IMAGE_STORAGE_BUCKET") or "").strip() or None
    access_key_id = str(
        image_storage.get("access_key_id") or os.environ.get("WECHAT_MD_IMAGE_STORAGE_ACCESS_KEY_ID") or ""
    ).strip() or None
    secret_access_key = str(
        image_storage.get("secret_access_key") or os.environ.get("WECHAT_MD_IMAGE_STORAGE_SECRET_ACCESS_KEY") or ""
    ).strip() or None
    path_template = str(
        image_storage.get("path_template") or os.environ.get("WECHAT_MD_IMAGE_STORAGE_PATH_TEMPLATE") or ""
    ).strip() or None
    public_base_url = str(
        image_storage.get("public_base_url") or os.environ.get("WECHAT_MD_IMAGE_STORAGE_PUBLIC_BASE_URL") or ""
    ).strip() or None
    telegram_enabled = _as_bool(telegram.get("enabled"), default=False)
    telegram_bot_token = str(telegram.get("bot_token") or os.environ.get("WECHAT_MD_TELEGRAM_BOT_TOKEN") or "").strip() or None
    telegram_webhook_public_base_url = str(
        telegram.get("webhook_public_base_url") or os.environ.get("WECHAT_MD_TELEGRAM_WEBHOOK_PUBLIC_BASE_URL") or ""
    ).strip() or None
    telegram_webhook_secret = str(
        telegram.get("webhook_secret") or os.environ.get("WECHAT_MD_TELEGRAM_WEBHOOK_SECRET") or ""
    ).strip() or None
    telegram_allowed_chat_ids = tuple(_normalize_chat_ids(telegram.get("allowed_chat_ids") or os.environ.get("WECHAT_MD_TELEGRAM_ALLOWED_CHAT_IDS")))
    telegram_notify_on_complete = _as_bool(
        telegram.get("notify_on_complete"),
        default=DEFAULT_TELEGRAM_NOTIFY_ON_COMPLETE,
    )
    telegram_webhook_status = str(telegram.get("webhook_status") or "inactive").strip() or "inactive"
    telegram_webhook_message = str(telegram.get("webhook_message") or "").strip()
    telegram_image_mode = str(telegram.get("image_mode") or "").strip() or None
    feishu_enabled = _as_bool(feishu.get("enabled"), default=False)
    feishu_app_id = str(feishu.get("app_id") or os.environ.get("WECHAT_MD_FEISHU_APP_ID") or "").strip() or None
    feishu_app_secret = str(feishu.get("app_secret") or os.environ.get("WECHAT_MD_FEISHU_APP_SECRET") or "").strip() or None
    feishu_verification_token = str(feishu.get("verification_token") or os.environ.get("WECHAT_MD_FEISHU_VERIFICATION_TOKEN") or "").strip() or None
    feishu_encrypt_key = str(feishu.get("encrypt_key") or os.environ.get("WECHAT_MD_FEISHU_ENCRYPT_KEY") or "").strip() or None
    feishu_webhook_public_base_url = str(
        feishu.get("webhook_public_base_url") or os.environ.get("WECHAT_MD_FEISHU_WEBHOOK_PUBLIC_BASE_URL") or ""
    ).strip() or None
    if feishu_webhook_public_base_url:
        feishu_webhook_public_base_url = _normalize_feishu_webhook_public_base_url(feishu_webhook_public_base_url) or None
    feishu_allowed_open_ids = tuple(_normalize_identifier_list(feishu.get("allowed_open_ids") or os.environ.get("WECHAT_MD_FEISHU_ALLOWED_OPEN_IDS")))
    feishu_notify_on_complete = _as_bool(
        feishu.get("notify_on_complete"),
        default=DEFAULT_FEISHU_NOTIFY_ON_COMPLETE,
    )
    feishu_webhook_status = str(feishu.get("webhook_status") or "inactive").strip() or "inactive"
    feishu_webhook_message = str(feishu.get("webhook_message") or "").strip()
    feishu_image_mode = str(feishu.get("image_mode") or "").strip() or None
    wechat_mp_token = str(wechat_mp.get("token") or os.environ.get("WECHAT_MD_WECHAT_MP_TOKEN") or "").strip() or None
    wechat_mp_cookie = str(wechat_mp.get("cookie") or os.environ.get("WECHAT_MD_WECHAT_MP_COOKIE") or "").strip() or None
    ai_enabled = _as_bool(runtime_user_settings.get("ai_enabled"), default=False)
    ai_selected_provider, ai_selected_model = _resolve_selected_ai_objects(ai_registry)
    ai_base_url = str((ai_selected_provider or {}).get("base_url") or "").strip() or None
    ai_api_key = str((ai_selected_provider or {}).get("api_key") or "").strip() or None
    ai_model = str((ai_selected_model or {}).get("model_id") or DEFAULT_AI_MODEL).strip() or DEFAULT_AI_MODEL
    ai_prompt_template = str(runtime_user_settings.get("ai_prompt_template") or os.environ.get("WECHAT_MD_AI_PROMPT_TEMPLATE") or DEFAULT_AI_PROMPT_TEMPLATE)
    ai_frontmatter_template = str(runtime_user_settings.get("ai_frontmatter_template") or os.environ.get("WECHAT_MD_AI_FRONTMATTER_TEMPLATE") or DEFAULT_AI_FRONTMATTER_TEMPLATE)
    ai_body_template = str(runtime_user_settings.get("ai_body_template") or os.environ.get("WECHAT_MD_AI_BODY_TEMPLATE") or DEFAULT_AI_BODY_TEMPLATE)
    ai_context_template = str(runtime_user_settings.get("ai_context_template") or os.environ.get("WECHAT_MD_AI_CONTEXT_TEMPLATE") or DEFAULT_AI_CONTEXT_TEMPLATE)
    ai_allow_body_polish = _as_bool(runtime_user_settings.get("ai_allow_body_polish"), default=False)
    ai_enable_content_polish = _as_bool(runtime_user_settings.get("ai_enable_content_polish"), default=False)
    ai_content_polish_prompt = str(
        runtime_user_settings.get("ai_content_polish_prompt")
        or os.environ.get("WECHAT_MD_AI_CONTENT_POLISH_PROMPT")
        or DEFAULT_AI_CONTENT_POLISH_PROMPT
    )
    ai_template_source = _normalize_ai_template_source(runtime_user_settings.get("ai_template_source"))
    return Settings(
        default_output_dir=output_dir,
        runtime_config_path=runtime_config_path,
        username=str(user_block.get("username") or "admin"),
        password_hash=str(user_block.get("password_hash") or hash_password("admin")),
        session_secret=str(auth_block.get("session_secret") or generate_session_secret()),
        session_cookie_secure=session_cookie_secure_enabled(),
        fns_base_url=fns_base_url.rstrip("/") if fns_base_url else None,
        fns_token=fns_token,
        fns_vault=fns_vault,
        fns_target_dir=fns_target_dir.strip("/\\"),
        cleanup_temp_on_success=cleanup_temp_on_success,
        single_conversion_isolation_enabled=single_conversion_isolation_enabled,
        single_conversion_hard_timeout_seconds=single_conversion_hard_timeout_seconds,
        image_mode=image_mode,
        image_storage_provider=provider,
        image_storage_endpoint=endpoint.rstrip("/") if endpoint else None,
        image_storage_region=region,
        image_storage_bucket=bucket,
        image_storage_access_key_id=access_key_id,
        image_storage_secret_access_key=secret_access_key,
        image_storage_path_template=path_template,
        image_storage_public_base_url=public_base_url.rstrip("/") if public_base_url else None,
        telegram_enabled=telegram_enabled,
        telegram_bot_token=telegram_bot_token,
        telegram_webhook_public_base_url=telegram_webhook_public_base_url.rstrip("/") if telegram_webhook_public_base_url else None,
        telegram_webhook_secret=telegram_webhook_secret,
        telegram_allowed_chat_ids=telegram_allowed_chat_ids,
        telegram_notify_on_complete=telegram_notify_on_complete,
        telegram_webhook_status=telegram_webhook_status,
        telegram_webhook_message=telegram_webhook_message,
        telegram_image_mode=telegram_image_mode,
        feishu_enabled=feishu_enabled,
        feishu_app_id=feishu_app_id,
        feishu_app_secret=feishu_app_secret,
        feishu_verification_token=feishu_verification_token,
        feishu_encrypt_key=feishu_encrypt_key,
        feishu_webhook_public_base_url=feishu_webhook_public_base_url.rstrip("/") if feishu_webhook_public_base_url else None,
        feishu_allowed_open_ids=feishu_allowed_open_ids,
        feishu_notify_on_complete=feishu_notify_on_complete,
        feishu_webhook_status=feishu_webhook_status,
        feishu_webhook_message=feishu_webhook_message,
        feishu_image_mode=feishu_image_mode,
        ai_enabled=ai_enabled,
        ai_providers=tuple(dict(item) for item in ai_registry.get("providers", [])),
        ai_models=tuple(dict(item) for item in ai_registry.get("models", [])),
        ai_selected_model_id=str(ai_registry.get("selected_model_id") or ""),
        ai_selected_provider=dict(ai_selected_provider) if ai_selected_provider else None,
        ai_selected_model=dict(ai_selected_model) if ai_selected_model else None,
        ai_base_url=ai_base_url.rstrip("/") if ai_base_url else None,
        ai_api_key=ai_api_key,
        ai_model=ai_model,
        ai_prompt_template=ai_prompt_template,
        ai_frontmatter_template=ai_frontmatter_template,
        ai_body_template=ai_body_template,
        ai_context_template=ai_context_template,
        ai_allow_body_polish=ai_allow_body_polish,
        ai_enable_content_polish=ai_enable_content_polish,
        ai_content_polish_prompt=ai_content_polish_prompt,
        ai_template_source=ai_template_source,
        wechat_mp_token=wechat_mp_token,
        wechat_mp_cookie=wechat_mp_cookie,
    )


def _normalize_runtime_config(raw_data: dict[str, Any]) -> dict[str, Any]:
    auth_raw = raw_data.get("auth") if isinstance(raw_data.get("auth"), dict) else {}
    auth_user_raw = auth_raw.get("user") if isinstance(auth_raw.get("user"), dict) else {}
    username = str(auth_user_raw.get("username") or "").strip()
    password_hash = str(auth_user_raw.get("password_hash") or "").strip()
    if not username or not password_hash:
        generated_username, generated_password, was_generated = build_initial_admin_credentials()
        username = generated_username
        password_hash = hash_password(generated_password)
        if was_generated:
            emit_generated_admin_password(username, generated_password)

    session_secret = _load_secret_value(
        encrypted_value=auth_raw.get("session_secret_encrypted"),
        plaintext_value=auth_raw.get("session_secret"),
        field_name="session_secret",
        default_factory=generate_session_secret,
    )

    if "auth" not in raw_data and "user_settings" not in raw_data:
        flat_user_settings = dict(raw_data)
        user_settings = _normalize_user_settings(flat_user_settings)
    else:
        user_settings = _normalize_user_settings(raw_data.get("user_settings"))

    return {
        "auth": {
            "user": {
                "username": username,
                "password_hash": password_hash,
            },
            "session_secret": session_secret,
        },
        "user_settings": user_settings,
    }


def _normalize_user_settings(raw_settings: Any) -> dict[str, Any]:
    source = raw_settings if isinstance(raw_settings, dict) else {}
    image_storage_source = source.get("image_storage") if isinstance(source.get("image_storage"), dict) else {}
    telegram_source = source.get("telegram") if isinstance(source.get("telegram"), dict) else {}
    feishu_source = source.get("feishu") if isinstance(source.get("feishu"), dict) else {}
    wechat_mp_source = source.get("wechat_mp") if isinstance(source.get("wechat_mp"), dict) else {}
    ai_registry = _normalize_ai_registry(source)
    return {
        "fns_base_url": str(source.get("fns_base_url") or "").strip(),
        "fns_token": _load_secret_value(
            encrypted_value=source.get("fns_token_encrypted"),
            plaintext_value=source.get("fns_token"),
            field_name="fns_token",
        ),
        "fns_vault": str(source.get("fns_vault") or "").strip(),
        "fns_target_dir": str(source.get("fns_target_dir") or DEFAULT_FNS_TARGET_DIR).strip() or DEFAULT_FNS_TARGET_DIR,
        "cleanup_temp_on_success": _as_bool(source.get("cleanup_temp_on_success"), default=True),
        "single_conversion_isolation_enabled": _as_bool(source.get("single_conversion_isolation_enabled"), default=True),
        "single_conversion_hard_timeout_seconds": _as_int(
            source.get("single_conversion_hard_timeout_seconds"),
            default=DEFAULT_SINGLE_CONVERSION_HARD_TIMEOUT_SECONDS,
            minimum=1,
        ),
        "ai_enabled": _as_bool(source.get("ai_enabled"), default=False),
        "ai_prompt_template": str(source.get("ai_prompt_template") or DEFAULT_AI_PROMPT_TEMPLATE),
        "ai_frontmatter_template": str(source.get("ai_frontmatter_template") or DEFAULT_AI_FRONTMATTER_TEMPLATE),
        "ai_body_template": str(source.get("ai_body_template") or DEFAULT_AI_BODY_TEMPLATE),
        "ai_context_template": str(source.get("ai_context_template") or DEFAULT_AI_CONTEXT_TEMPLATE),
        "ai_allow_body_polish": _as_bool(source.get("ai_allow_body_polish"), default=False),
        "ai_enable_content_polish": _as_bool(source.get("ai_enable_content_polish"), default=False),
        "ai_content_polish_prompt": str(source.get("ai_content_polish_prompt") or DEFAULT_AI_CONTENT_POLISH_PROMPT),
        "ai_template_source": _normalize_ai_template_source(source.get("ai_template_source")),
        "ai": ai_registry,
        "image_mode": _normalize_image_mode(source.get("image_mode")),
        "image_storage": {
            "provider": str(image_storage_source.get("provider") or "s3").strip() or "s3",
            "endpoint": str(image_storage_source.get("endpoint") or "").strip(),
            "region": str(image_storage_source.get("region") or "").strip(),
            "bucket": str(image_storage_source.get("bucket") or "").strip(),
            "access_key_id": str(image_storage_source.get("access_key_id") or "").strip(),
            "secret_access_key": _load_secret_value(
                encrypted_value=image_storage_source.get("secret_access_key_encrypted"),
                plaintext_value=image_storage_source.get("secret_access_key"),
                field_name="image_storage.secret_access_key",
            ),
            "path_template": str(image_storage_source.get("path_template") or "").strip(),
            "public_base_url": str(image_storage_source.get("public_base_url") or "").strip(),
        },
        "telegram": {
            "enabled": _as_bool(telegram_source.get("enabled"), default=False),
            "bot_token": _load_secret_value(
                encrypted_value=telegram_source.get("bot_token_encrypted"),
                plaintext_value=telegram_source.get("bot_token"),
                field_name="telegram.bot_token",
            ),
            "webhook_public_base_url": str(telegram_source.get("webhook_public_base_url") or "").strip(),
            "webhook_secret": _load_secret_value(
                encrypted_value=telegram_source.get("webhook_secret_encrypted"),
                plaintext_value=telegram_source.get("webhook_secret"),
                field_name="telegram.webhook_secret",
            ),
            "allowed_chat_ids": _normalize_chat_ids(telegram_source.get("allowed_chat_ids")),
            "notify_on_complete": _as_bool(telegram_source.get("notify_on_complete"), default=DEFAULT_TELEGRAM_NOTIFY_ON_COMPLETE),
            "webhook_status": str(telegram_source.get("webhook_status") or "inactive").strip() or "inactive",
            "webhook_message": str(telegram_source.get("webhook_message") or "").strip(),
            "image_mode": _normalize_entry_image_mode(telegram_source.get("image_mode")),
        },
        "feishu": {
            "enabled": _as_bool(feishu_source.get("enabled"), default=False),
            "app_id": str(feishu_source.get("app_id") or "").strip(),
            "app_secret": _load_secret_value(
                encrypted_value=feishu_source.get("app_secret_encrypted"),
                plaintext_value=feishu_source.get("app_secret"),
                field_name="feishu.app_secret",
            ),
            "verification_token": _load_secret_value(
                encrypted_value=feishu_source.get("verification_token_encrypted"),
                plaintext_value=feishu_source.get("verification_token"),
                field_name="feishu.verification_token",
            ),
            "encrypt_key": _load_secret_value(
                encrypted_value=feishu_source.get("encrypt_key_encrypted"),
                plaintext_value=feishu_source.get("encrypt_key"),
                field_name="feishu.encrypt_key",
            ),
            "webhook_public_base_url": str(feishu_source.get("webhook_public_base_url") or "").strip(),
            "allowed_open_ids": _normalize_identifier_list(feishu_source.get("allowed_open_ids")),
            "notify_on_complete": _as_bool(feishu_source.get("notify_on_complete"), default=DEFAULT_FEISHU_NOTIFY_ON_COMPLETE),
            "webhook_status": str(feishu_source.get("webhook_status") or "inactive").strip() or "inactive",
            "webhook_message": str(feishu_source.get("webhook_message") or "").strip(),
            "image_mode": _normalize_entry_image_mode(feishu_source.get("image_mode")),
        },
        "wechat_mp": {
            "token": _load_secret_value(
                encrypted_value=wechat_mp_source.get("token_encrypted"),
                plaintext_value=wechat_mp_source.get("token"),
                field_name="wechat_mp.token",
            ),
            "cookie": _load_secret_value(
                encrypted_value=wechat_mp_source.get("cookie_encrypted"),
                plaintext_value=wechat_mp_source.get("cookie"),
                field_name="wechat_mp.cookie",
            ),
        },
    }


def _write_runtime_config(config_path: Path, data: dict[str, Any]) -> None:
    serialized = _serialize_runtime_config(data)
    config_path.write_text(json.dumps(serialized, ensure_ascii=False, indent=2), encoding="utf-8")


def _serialize_runtime_config(data: dict[str, Any]) -> dict[str, Any]:
    auth_block = data["auth"]
    user_settings = data["user_settings"]
    image_storage = user_settings["image_storage"]
    telegram = user_settings["telegram"]
    feishu = user_settings["feishu"]
    wechat_mp = user_settings.get("wechat_mp") if isinstance(user_settings.get("wechat_mp"), dict) else {}
    ai_registry = user_settings["ai"]
    return {
        "auth": {
            "user": {
                "username": str(auth_block["user"]["username"]),
                "password_hash": str(auth_block["user"]["password_hash"]),
            },
            "session_secret_encrypted": encrypt_secret(str(auth_block.get("session_secret") or generate_session_secret())),
        },
        "user_settings": {
            "fns_base_url": str(user_settings.get("fns_base_url") or "").strip(),
            "fns_token_encrypted": encrypt_secret(str(user_settings.get("fns_token") or "")),
            "fns_vault": str(user_settings.get("fns_vault") or "").strip(),
            "fns_target_dir": str(user_settings.get("fns_target_dir") or DEFAULT_FNS_TARGET_DIR).strip() or DEFAULT_FNS_TARGET_DIR,
            "cleanup_temp_on_success": _as_bool(user_settings.get("cleanup_temp_on_success"), default=True),
            "single_conversion_isolation_enabled": _as_bool(
                user_settings.get("single_conversion_isolation_enabled"),
                default=True,
            ),
            "single_conversion_hard_timeout_seconds": _as_int(
                user_settings.get("single_conversion_hard_timeout_seconds"),
                default=DEFAULT_SINGLE_CONVERSION_HARD_TIMEOUT_SECONDS,
                minimum=1,
            ),
            "ai_enabled": _as_bool(user_settings.get("ai_enabled"), default=False),
            "ai_prompt_template": str(user_settings.get("ai_prompt_template") or DEFAULT_AI_PROMPT_TEMPLATE),
            "ai_frontmatter_template": str(user_settings.get("ai_frontmatter_template") or DEFAULT_AI_FRONTMATTER_TEMPLATE),
            "ai_body_template": str(user_settings.get("ai_body_template") or DEFAULT_AI_BODY_TEMPLATE),
            "ai_context_template": str(user_settings.get("ai_context_template") or DEFAULT_AI_CONTEXT_TEMPLATE),
            "ai_allow_body_polish": _as_bool(user_settings.get("ai_allow_body_polish"), default=False),
            "ai_enable_content_polish": _as_bool(user_settings.get("ai_enable_content_polish"), default=False),
            "ai_content_polish_prompt": str(user_settings.get("ai_content_polish_prompt") or DEFAULT_AI_CONTENT_POLISH_PROMPT),
            "ai_template_source": _normalize_ai_template_source(user_settings.get("ai_template_source")),
            "ai": _serialize_ai_registry(ai_registry),
            "image_mode": _normalize_image_mode(user_settings.get("image_mode")),
            "image_storage": {
                "provider": str(image_storage.get("provider") or "s3").strip() or "s3",
                "endpoint": str(image_storage.get("endpoint") or "").strip(),
                "region": str(image_storage.get("region") or "").strip(),
                "bucket": str(image_storage.get("bucket") or "").strip(),
                "access_key_id": str(image_storage.get("access_key_id") or "").strip(),
                "secret_access_key_encrypted": encrypt_secret(str(image_storage.get("secret_access_key") or "")),
                "path_template": str(image_storage.get("path_template") or "").strip(),
                "public_base_url": str(image_storage.get("public_base_url") or "").strip(),
            },
            "telegram": {
                "enabled": _as_bool(telegram.get("enabled"), default=False),
                "bot_token_encrypted": encrypt_secret(str(telegram.get("bot_token") or "")),
                "webhook_public_base_url": str(telegram.get("webhook_public_base_url") or "").strip(),
                "webhook_secret_encrypted": encrypt_secret(str(telegram.get("webhook_secret") or "")),
                "allowed_chat_ids": _normalize_chat_ids(telegram.get("allowed_chat_ids")),
                "notify_on_complete": _as_bool(telegram.get("notify_on_complete"), default=DEFAULT_TELEGRAM_NOTIFY_ON_COMPLETE),
                "webhook_status": str(telegram.get("webhook_status") or "inactive").strip() or "inactive",
                "webhook_message": str(telegram.get("webhook_message") or "").strip(),
                "image_mode": _normalize_entry_image_mode(telegram.get("image_mode")),
            },
            "feishu": {
                "enabled": _as_bool(feishu.get("enabled"), default=False),
                "app_id": str(feishu.get("app_id") or "").strip(),
                "app_secret_encrypted": encrypt_secret(str(feishu.get("app_secret") or "")),
                "verification_token_encrypted": encrypt_secret(str(feishu.get("verification_token") or "")),
                "encrypt_key_encrypted": encrypt_secret(str(feishu.get("encrypt_key") or "")),
                "webhook_public_base_url": _normalize_feishu_webhook_public_base_url(feishu.get("webhook_public_base_url")),
                "allowed_open_ids": _normalize_identifier_list(feishu.get("allowed_open_ids")),
                "notify_on_complete": _as_bool(feishu.get("notify_on_complete"), default=DEFAULT_FEISHU_NOTIFY_ON_COMPLETE),
                "webhook_status": str(feishu.get("webhook_status") or "inactive").strip() or "inactive",
                "webhook_message": str(feishu.get("webhook_message") or "").strip(),
                "image_mode": _normalize_entry_image_mode(feishu.get("image_mode")),
            },
            "wechat_mp": {
                "token_encrypted": encrypt_secret(str(wechat_mp.get("token") or "")),
                "cookie_encrypted": encrypt_secret(str(wechat_mp.get("cookie") or "")),
            },
        },
    }


def _load_secret_value(
    encrypted_value: Any,
    plaintext_value: Any,
    field_name: str,
    default_factory=None,
) -> str:
    encrypted = str(encrypted_value or "").strip()
    plaintext = str(plaintext_value or "").strip()
    if encrypted:
        try:
            return decrypt_secret(encrypted)
        except RuntimeError as error:
            raise RuntimeError(f"无法读取敏感字段 {field_name}: {error}") from error
    if plaintext:
        return plaintext
    if default_factory is not None:
        return str(default_factory())
    return ""


def _normalize_image_mode(value: Any) -> str:
    normalized = str(value or DEFAULT_IMAGE_MODE).strip()
    return normalized if normalized in IMAGE_MODE_VALUES else DEFAULT_IMAGE_MODE


def _normalize_entry_image_mode(value: Any) -> str:
    """Normalize per-entry image mode. Returns empty string for 'follow global'."""
    normalized = str(value or "").strip()
    return normalized if normalized in IMAGE_MODE_VALUES else ""


def _normalize_ai_template_source(value: Any) -> str:
    normalized = str(value or "manual").strip()
    return normalized if normalized in AI_TEMPLATE_SOURCE_VALUES else "manual"


def _build_builtin_ai_providers() -> list[dict[str, Any]]:
    return [dict(item) for item in BUILTIN_AI_PROVIDER_DEFINITIONS]


def _normalize_ai_registry(source: dict[str, Any]) -> dict[str, Any]:
    ai_source = source.get("ai") if isinstance(source.get("ai"), dict) else {}
    raw_providers = ai_source.get("providers") if isinstance(ai_source.get("providers"), list) else None
    raw_models = ai_source.get("models") if isinstance(ai_source.get("models"), list) else None
    raw_selected_model_id = str(ai_source.get("selected_model_id") or "").strip()

    built_in_map = {item["id"]: dict(item) for item in BUILTIN_AI_PROVIDER_DEFINITIONS}
    existing_provider_map: dict[str, dict[str, Any]] = {}
    if isinstance(raw_providers, list):
        for item in raw_providers:
            if isinstance(item, dict):
                existing_provider_map[str(item.get("id") or "").strip()] = item

    providers: list[dict[str, Any]] = []
    for provider_id, definition in built_in_map.items():
        merged = {**definition, **dict(existing_provider_map.get(provider_id) or {})}
        providers.append(_normalize_ai_provider(merged, built_in_definition=definition))

    if isinstance(raw_providers, list):
        for index, item in enumerate(raw_providers, start=1):
            if not isinstance(item, dict):
                continue
            provider_id = str(item.get("id") or "").strip()
            if provider_id in built_in_map:
                continue
            providers.append(_normalize_ai_provider(item, fallback_id=f"custom-provider-{index}"))
    else:
        legacy_base_url = str(source.get("ai_base_url") or "").strip()
        legacy_api_key = _load_secret_value(
            encrypted_value=source.get("ai_api_key_encrypted"),
            plaintext_value=source.get("ai_api_key"),
            field_name="ai_api_key",
        )
        legacy_model = str(source.get("ai_model") or DEFAULT_AI_MODEL).strip() or DEFAULT_AI_MODEL
        if legacy_base_url or legacy_api_key or legacy_model:
            for provider in providers:
                if provider["id"] == "openai-compatible-default":
                    provider["base_url"] = legacy_base_url
                    provider["api_key"] = legacy_api_key
                    break
            raw_models = [
                {
                    "id": "model-openai-compatible-default",
                    "provider_id": "openai-compatible-default",
                    "display_name": legacy_model,
                    "model_id": legacy_model,
                    "enabled": True,
                }
            ]
            raw_selected_model_id = "model-openai-compatible-default"

    models: list[dict[str, Any]] = []
    seen_model_ids: set[str] = set()
    if isinstance(raw_models, list):
        for index, item in enumerate(raw_models, start=1):
            if not isinstance(item, dict):
                continue
            normalized = _normalize_ai_model(item, fallback_id=f"model-{index}")
            model_id = str(normalized.get("id") or "").strip()
            if not model_id or model_id in seen_model_ids:
                continue
            seen_model_ids.add(model_id)
            models.append(normalized)

    if not models:
        fallback_provider = next((item for item in providers if item["id"] == "openai-compatible-default"), providers[0])
        fallback_model_id = "model-openai-compatible-default"
        models.append(
            {
                "id": fallback_model_id,
                "provider_id": fallback_provider["id"],
                "display_name": DEFAULT_AI_MODEL,
                "model_id": DEFAULT_AI_MODEL,
                "enabled": True,
            }
        )
        raw_selected_model_id = fallback_model_id

    selected_model_id = raw_selected_model_id if any(item["id"] == raw_selected_model_id for item in models) else ""
    if not selected_model_id:
        selected_model_id = next((item["id"] for item in models if item.get("enabled", True)), models[0]["id"])

    return {
        "providers": providers,
        "models": models,
        "selected_model_id": selected_model_id,
    }


def _normalize_ai_provider(
    raw_provider: dict[str, Any],
    *,
    built_in_definition: dict[str, Any] | None = None,
    fallback_id: str | None = None,
) -> dict[str, Any]:
    provider_id = str(raw_provider.get("id") or fallback_id or "").strip() or "custom-provider"
    provider_type = str(raw_provider.get("type") or "openai_compatible").strip()
    if built_in_definition is not None:
        provider_id = str(built_in_definition["id"])
        provider_type = str(built_in_definition["type"])
        display_name = str(raw_provider.get("display_name") or built_in_definition["display_name"]).strip() or str(built_in_definition["display_name"])
        built_in = True
        default_base_url = str(built_in_definition.get("base_url") or "").strip()
    else:
        if provider_type not in AI_PROVIDER_TYPE_VALUES or provider_type not in {"openai_compatible", "anthropic", "gemini", "ollama", "openrouter"}:
            provider_type = "openai_compatible"
        display_name = str(raw_provider.get("display_name") or provider_id).strip() or provider_id
        built_in = False
        default_base_url = ""

    base_url = str(raw_provider.get("base_url") or default_base_url).strip()
    api_key = _load_secret_value(
        encrypted_value=raw_provider.get("api_key_encrypted"),
        plaintext_value=raw_provider.get("api_key"),
        field_name=f"ai.providers.{provider_id}.api_key",
    )
    return {
        "id": provider_id,
        "type": provider_type,
        "display_name": display_name,
        "built_in": built_in,
        "enabled": _as_bool(raw_provider.get("enabled"), default=True),
        "base_url": base_url,
        "api_key": api_key,
    }


def _normalize_ai_model(raw_model: dict[str, Any], *, fallback_id: str) -> dict[str, Any]:
    model_id = str(raw_model.get("id") or fallback_id).strip() or fallback_id
    model_name = str(raw_model.get("model_id") or raw_model.get("display_name") or DEFAULT_AI_MODEL).strip() or DEFAULT_AI_MODEL
    return {
        "id": model_id,
        "provider_id": str(raw_model.get("provider_id") or "openai-compatible-default").strip() or "openai-compatible-default",
        "display_name": str(raw_model.get("display_name") or model_name).strip() or model_name,
        "model_id": model_name,
        "enabled": _as_bool(raw_model.get("enabled"), default=True),
    }


def _resolve_selected_ai_objects(ai_registry: dict[str, Any]) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    providers = [item for item in ai_registry.get("providers", []) if isinstance(item, dict)]
    models = [item for item in ai_registry.get("models", []) if isinstance(item, dict)]
    selected_model_id = str(ai_registry.get("selected_model_id") or "").strip()
    model = next((item for item in models if str(item.get("id") or "") == selected_model_id), None)
    if model is None:
        model = next((item for item in models if item.get("enabled", True)), None)
    if model is None:
        return None, None
    provider = next((item for item in providers if str(item.get("id") or "") == str(model.get("provider_id") or "")), None)
    return provider, model


def _serialize_ai_registry(ai_registry: dict[str, Any]) -> dict[str, Any]:
    providers = []
    for provider in ai_registry.get("providers", []):
        if not isinstance(provider, dict):
            continue
        providers.append(
            {
                "id": str(provider.get("id") or "").strip(),
                "type": str(provider.get("type") or "openai_compatible").strip(),
                "display_name": str(provider.get("display_name") or "").strip(),
                "built_in": bool(provider.get("built_in")),
                "enabled": _as_bool(provider.get("enabled"), default=True),
                "base_url": str(provider.get("base_url") or "").strip(),
                "api_key_encrypted": encrypt_secret(str(provider.get("api_key") or "")),
            }
        )
    models = []
    for model in ai_registry.get("models", []):
        if not isinstance(model, dict):
            continue
        models.append(
            {
                "id": str(model.get("id") or "").strip(),
                "provider_id": str(model.get("provider_id") or "").strip(),
                "display_name": str(model.get("display_name") or "").strip(),
                "model_id": str(model.get("model_id") or "").strip(),
                "enabled": _as_bool(model.get("enabled"), default=True),
            }
        )
    return {
        "providers": providers,
        "models": models,
        "selected_model_id": str(ai_registry.get("selected_model_id") or "").strip(),
    }


def _sanitize_provider_for_payload(provider: dict[str, Any] | None) -> dict[str, Any] | None:
    if provider is None:
        return None
    return {
        "id": str(provider.get("id") or "").strip(),
        "type": str(provider.get("type") or "").strip(),
        "display_name": str(provider.get("display_name") or "").strip(),
        "built_in": bool(provider.get("built_in")),
        "enabled": _as_bool(provider.get("enabled"), default=True),
        "base_url": str(provider.get("base_url") or "").strip(),
        "api_key_configured": bool(str(provider.get("api_key") or "").strip()),
        "api_key_masked": _mask_secret(str(provider.get("api_key") or "").strip()),
    }


def _validate_ai_provider(provider: dict[str, Any]) -> None:
    provider_type = str(provider.get("type") or "").strip()
    if provider_type not in AI_PROVIDER_TYPE_VALUES:
        raise ValueError(f"AI provider 类型无效: {provider_type}")
    base_url = str(provider.get("base_url") or "").strip()
    if base_url and not base_url.startswith(("http://", "https://")):
        raise ValueError(f"AI provider {provider.get('display_name') or provider.get('id')} 的 Base URL 必须以 http:// 或 https:// 开头")


def _validate_ai_model(model: dict[str, Any], ai_registry: dict[str, Any]) -> None:
    if not str(model.get("display_name") or "").strip():
        raise ValueError("AI 模型 display_name 不能为空")
    if not str(model.get("model_id") or "").strip():
        raise ValueError("AI 模型 model_id 不能为空")
    provider_ids = {str(item.get("id") or "") for item in ai_registry.get("providers", []) if isinstance(item, dict)}
    if str(model.get("provider_id") or "") not in provider_ids:
        raise ValueError(f"AI 模型引用了不存在的 provider: {model.get('provider_id')}")


def _validate_ai_provider_runtime_requirements(provider: dict[str, Any] | None) -> None:
    if provider is None:
        raise ValueError("AI provider 未配置")
    provider_type = str(provider.get("type") or "").strip()
    base_url = str(provider.get("base_url") or "").strip()
    api_key = str(provider.get("api_key") or "").strip()
    if provider_type in {"openai_compatible", "openrouter", "ollama", "custom"} and not base_url:
        raise ValueError("AI provider Base URL 未配置")
    if provider_type in {"openrouter", "anthropic", "gemini"} and not api_key:
        raise ValueError("AI provider API Key 未配置")


def _mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _validate_runtime_config(data: dict[str, Any]) -> None:
    user_settings = data["user_settings"]
    base_url = str(user_settings.get("fns_base_url") or "").strip()
    if base_url and not base_url.startswith(("http://", "https://")):
        raise ValueError("FNS 基础地址必须以 http:// 或 https:// 开头")

    ai_enabled = _as_bool(user_settings.get("ai_enabled"), default=False)
    ai_registry = user_settings.get("ai") if isinstance(user_settings.get("ai"), dict) else _normalize_ai_registry(user_settings)
    selected_provider, selected_model = _resolve_selected_ai_objects(ai_registry)
    for provider in ai_registry.get("providers", []):
        _validate_ai_provider(provider)
    for model in ai_registry.get("models", []):
        _validate_ai_model(model, ai_registry)
    if ai_enabled:
        missing_ai = []
        if selected_provider is None:
            missing_ai.append("ai_selected_provider")
        elif not _as_bool(selected_provider.get("enabled"), default=True):
            missing_ai.append("ai_selected_provider_disabled")
        if selected_model is None:
            missing_ai.append("ai_selected_model_id")
        elif not _as_bool(selected_model.get("enabled"), default=True):
            missing_ai.append("ai_selected_model_disabled")
        if not str(user_settings.get("ai_prompt_template") or "").strip():
            missing_ai.append("ai_prompt_template")
        if not str(user_settings.get("ai_frontmatter_template") or "").strip():
            missing_ai.append("ai_frontmatter_template")
        if not str(user_settings.get("ai_body_template") or "").strip():
            missing_ai.append("ai_body_template")
        if not str(user_settings.get("ai_context_template") or "").strip():
            missing_ai.append("ai_context_template")
        if _as_bool(user_settings.get("ai_enable_content_polish"), default=False) and not str(user_settings.get("ai_content_polish_prompt") or "").strip():
            missing_ai.append("ai_content_polish_prompt")
        if missing_ai:
            raise ValueError("AI 润色配置不完整，缺少字段: " + ", ".join(missing_ai))
        _validate_ai_provider_runtime_requirements(selected_provider)

    telegram = user_settings["telegram"]
    telegram_webhook_public_base_url = str(telegram.get("webhook_public_base_url") or "").strip()
    if telegram_webhook_public_base_url and not telegram_webhook_public_base_url.startswith(("http://", "https://")):
        raise ValueError("Telegram Webhook 对外基础地址必须以 http:// 或 https:// 开头")
    if _as_bool(telegram.get("enabled"), default=False):
        missing_telegram = []
        if not str(telegram.get("bot_token") or "").strip():
            missing_telegram.append("bot_token")
        if not telegram_webhook_public_base_url:
            missing_telegram.append("webhook_public_base_url")
        if not str(telegram.get("webhook_secret") or "").strip():
            missing_telegram.append("webhook_secret")
        if not _normalize_chat_ids(telegram.get("allowed_chat_ids")):
            missing_telegram.append("allowed_chat_ids")
        if missing_telegram:
            raise ValueError("Telegram Bot 配置不完整，缺少字段: " + ", ".join(missing_telegram))

    feishu = user_settings["feishu"]
    feishu_webhook_public_base_url = _normalize_feishu_webhook_public_base_url(feishu.get("webhook_public_base_url"))
    if feishu_webhook_public_base_url and not feishu_webhook_public_base_url.startswith(("http://", "https://")):
        raise ValueError("飞书 Webhook 对外基础地址必须以 http:// 或 https:// 开头")
    if _as_bool(feishu.get("enabled"), default=False):
        missing_feishu = []
        if not str(feishu.get("app_id") or "").strip():
            missing_feishu.append("app_id")
        if not str(feishu.get("app_secret") or "").strip():
            missing_feishu.append("app_secret")
        if not str(feishu.get("verification_token") or "").strip():
            missing_feishu.append("verification_token")
        if not feishu_webhook_public_base_url:
            missing_feishu.append("webhook_public_base_url")
        if missing_feishu:
            raise ValueError("飞书 Bot 配置不完整，缺少字段: " + ", ".join(missing_feishu))

    image_mode = user_settings.get("image_mode")
    if image_mode not in IMAGE_MODE_VALUES:
        raise ValueError("图片模式仅支持 wechat_hotlink 或 s3_hotlink")
    if image_mode != "s3_hotlink":
        return

    image_storage = user_settings["image_storage"]
    required_fields = {
        "endpoint": image_storage.get("endpoint"),
        "region": image_storage.get("region"),
        "bucket": image_storage.get("bucket"),
        "access_key_id": image_storage.get("access_key_id"),
        "secret_access_key": image_storage.get("secret_access_key"),
        "path_template": image_storage.get("path_template"),
        "public_base_url": image_storage.get("public_base_url"),
    }
    missing = [name for name, value in required_fields.items() if not str(value or "").strip()]
    if missing:
        raise ValueError("S3 图床配置不完整，缺少字段: " + ", ".join(missing))
    for field_name in ("endpoint", "public_base_url"):
        if not str(required_fields[field_name]).startswith(("http://", "https://")):
            raise ValueError(f"S3 图床字段 {field_name} 必须以 http:// 或 https:// 开头")


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_int(value: Any, default: int, minimum: int | None = None) -> int:
    if value is None or value == "":
        result = int(default)
    else:
        try:
            result = int(value)
        except (TypeError, ValueError):
            result = int(default)
    if minimum is not None:
        result = max(result, int(minimum))
    return result


def _normalize_chat_ids(value: Any) -> list[str]:
    return _normalize_identifier_list(value)


def _normalize_identifier_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw_parts = [str(item).strip() for item in value]
    else:
        text = str(value).replace(",", "\n")
        raw_parts = [part.strip() for part in text.splitlines()]
    deduped: list[str] = []
    seen: set[str] = set()
    for part in raw_parts:
        if not part or part in seen:
            continue
        seen.add(part)
        deduped.append(part)
    return deduped


def _normalize_feishu_webhook_public_base_url(value: Any) -> str:
    raw = str(value or "").strip().rstrip("/")
    if not raw:
        return ""
    if raw.endswith(FEISHU_WEBHOOK_PATH):
        return raw[: -len(FEISHU_WEBHOOK_PATH)].rstrip("/")
    return raw
