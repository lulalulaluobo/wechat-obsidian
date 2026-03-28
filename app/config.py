from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.auth import generate_session_secret, hash_password


DEFAULT_USERNAME = "admin"
DEFAULT_PASSWORD = "admin"
DEFAULT_FNS_TARGET_DIR = "00_Inbox/微信公众号"


@dataclass(frozen=True)
class Settings:
    default_output_dir: Path
    default_r2_config_path: Path
    runtime_config_path: Path
    username: str
    password_hash: str
    session_secret: str
    default_timeout: int = 30
    fns_base_url: str | None = None
    fns_token: str | None = None
    fns_vault: str | None = None
    fns_target_dir: str = DEFAULT_FNS_TARGET_DIR
    cleanup_temp_on_success: bool = True

    @property
    def fns_enabled(self) -> bool:
        return bool(self.fns_base_url and self.fns_token and self.fns_vault)


FNS_FIELDS = {
    "fns_base_url",
    "fns_token",
    "fns_vault",
    "fns_target_dir",
    "cleanup_temp_on_success",
}
SECRET_FIELDS = {"fns_token"}


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
    config_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return normalized


def save_runtime_config(payload: dict[str, Any], clear_fields: list[str] | None = None) -> dict[str, Any]:
    config_path = get_runtime_config_path()
    current = load_runtime_config(config_path)
    updated = _normalize_runtime_config(current)
    clear_set = {field for field in (clear_fields or []) if field in SECRET_FIELDS}
    user_settings = dict(updated["user_settings"])

    for field in clear_set:
        user_settings[field] = ""

    for field in FNS_FIELDS:
        if field not in payload:
            continue
        raw_value = payload.get(field)
        if field == "cleanup_temp_on_success":
            user_settings[field] = _as_bool(raw_value, default=True)
            continue
        if raw_value is None:
            continue
        value = str(raw_value).strip()
        if field in SECRET_FIELDS:
            if value:
                user_settings[field] = value
            continue
        user_settings[field] = value

    updated["user_settings"] = _normalize_user_settings(user_settings)
    _validate_runtime_config(updated)
    config_path.write_text(
        json.dumps(updated, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return updated


def update_password(current_password: str, new_password: str) -> dict[str, Any]:
    config_path = get_runtime_config_path()
    current = load_runtime_config(config_path)
    auth_user = current["auth"]["user"]
    if not _verify_current_password(current_password, str(auth_user.get("password_hash") or "")):
        raise ValueError("当前密码不正确")

    normalized_password = (new_password or "").strip()
    if not normalized_password:
        raise ValueError("新密码不能为空")

    auth_user["password_hash"] = hash_password(normalized_password)
    current["auth"]["user"] = auth_user
    config_path.write_text(
        json.dumps(current, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return current


def build_admin_settings_payload() -> dict[str, Any]:
    settings = get_settings()
    runtime_values = load_runtime_config(settings.runtime_config_path)
    runtime_overrides = [
        "auth.user.username",
        "auth.user.password_hash",
        "auth.session_secret",
        *[f"user_settings.{key}" for key in sorted(runtime_values["user_settings"].keys())],
    ]
    return {
        "runtime_config_path": str(settings.runtime_config_path),
        "auth_enabled": True,
        "default_output_target": "fns" if settings.fns_enabled else "local",
        "current_user": {"username": settings.username},
        "fns_base_url": settings.fns_base_url or "",
        "fns_vault": settings.fns_vault or "",
        "fns_target_dir": settings.fns_target_dir or DEFAULT_FNS_TARGET_DIR,
        "fns_token_configured": bool(settings.fns_token),
        "fns_token_masked": _mask_secret(settings.fns_token),
        "cleanup_temp_on_success": settings.cleanup_temp_on_success,
        "runtime_overrides": runtime_overrides,
    }


def get_settings() -> Settings:
    runtime_config_path = get_runtime_config_path()
    runtime_values = load_runtime_config(runtime_config_path)
    auth_block = runtime_values["auth"]
    user_block = auth_block["user"]
    runtime_user_settings = runtime_values["user_settings"]

    output_dir = Path(
        os.environ.get("WECHAT_MD_DEFAULT_OUTPUT_DIR", r"D:\obsidian\00_Inbox")
    ).resolve()
    r2_config_path = Path(
        os.environ.get(
            "WECHAT_MD_R2_CONFIG_PATH",
            r"D:\obsidian\.obsidian\plugins\image-upload-toolkit\data.json",
        )
    ).resolve()
    fns_base_url = (
        str(runtime_user_settings.get("fns_base_url") or os.environ.get("WECHAT_MD_FNS_BASE_URL") or "").strip() or None
    )
    fns_token = (
        str(runtime_user_settings.get("fns_token") or os.environ.get("WECHAT_MD_FNS_TOKEN") or "").strip() or None
    )
    fns_vault = (
        str(runtime_user_settings.get("fns_vault") or os.environ.get("WECHAT_MD_FNS_VAULT") or "").strip() or None
    )
    fns_target_dir = (
        str(runtime_user_settings.get("fns_target_dir") or os.environ.get("WECHAT_MD_FNS_TARGET_DIR") or DEFAULT_FNS_TARGET_DIR).strip()
        or DEFAULT_FNS_TARGET_DIR
    )
    cleanup_temp_on_success = _as_bool(
        runtime_user_settings.get("cleanup_temp_on_success"),
        default=True,
    )
    return Settings(
        default_output_dir=output_dir,
        default_r2_config_path=r2_config_path,
        runtime_config_path=runtime_config_path,
        username=str(user_block.get("username") or DEFAULT_USERNAME),
        password_hash=str(user_block.get("password_hash") or hash_password(DEFAULT_PASSWORD)),
        session_secret=str(auth_block.get("session_secret") or generate_session_secret()),
        fns_base_url=fns_base_url.rstrip("/") if fns_base_url else None,
        fns_token=fns_token,
        fns_vault=fns_vault,
        fns_target_dir=fns_target_dir.strip("/\\"),
        cleanup_temp_on_success=cleanup_temp_on_success,
    )


def _normalize_runtime_config(raw_data: dict[str, Any]) -> dict[str, Any]:
    auth_defaults = {
        "user": {
            "username": DEFAULT_USERNAME,
            "password_hash": hash_password(DEFAULT_PASSWORD),
        },
        "session_secret": generate_session_secret(),
    }
    default_payload = {
        "auth": auth_defaults,
        "user_settings": _normalize_user_settings({}),
    }

    if "auth" not in raw_data and "user_settings" not in raw_data:
        flat_user_settings = {key: raw_data.get(key) for key in FNS_FIELDS if key in raw_data}
        default_payload["user_settings"] = _normalize_user_settings(flat_user_settings)
        return default_payload

    auth_raw = raw_data.get("auth") if isinstance(raw_data.get("auth"), dict) else {}
    auth_user_raw = auth_raw.get("user") if isinstance(auth_raw.get("user"), dict) else {}
    normalized_auth = {
        "user": {
            "username": str(auth_user_raw.get("username") or DEFAULT_USERNAME),
            "password_hash": str(auth_user_raw.get("password_hash") or hash_password(DEFAULT_PASSWORD)),
        },
        "session_secret": str(auth_raw.get("session_secret") or generate_session_secret()),
    }
    normalized_user_settings = _normalize_user_settings(raw_data.get("user_settings"))
    return {
        "auth": normalized_auth,
        "user_settings": normalized_user_settings,
    }


def _normalize_user_settings(raw_settings: Any) -> dict[str, Any]:
    source = raw_settings if isinstance(raw_settings, dict) else {}
    return {
        "fns_base_url": str(source.get("fns_base_url") or "").strip(),
        "fns_token": str(source.get("fns_token") or "").strip(),
        "fns_vault": str(source.get("fns_vault") or "").strip(),
        "fns_target_dir": str(source.get("fns_target_dir") or DEFAULT_FNS_TARGET_DIR).strip() or DEFAULT_FNS_TARGET_DIR,
        "cleanup_temp_on_success": _as_bool(source.get("cleanup_temp_on_success"), default=True),
    }


def _mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _validate_runtime_config(data: dict[str, Any]) -> None:
    base_url = str(data["user_settings"].get("fns_base_url") or "").strip()
    if base_url and not base_url.startswith(("http://", "https://")):
        raise ValueError("FNS 基础地址必须以 http:// 或 https:// 开头")


def _verify_current_password(password: str, stored_hash: str) -> bool:
    from app.auth import verify_password

    return verify_password(password, stored_hash)


def _as_bool(value: Any, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
