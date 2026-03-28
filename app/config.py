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
IMAGE_MODE_VALUES = {"wechat_hotlink", "s3_hotlink"}


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
    image_mode: str = DEFAULT_IMAGE_MODE
    image_storage_provider: str | None = None
    image_storage_endpoint: str | None = None
    image_storage_region: str | None = None
    image_storage_bucket: str | None = None
    image_storage_access_key_id: str | None = None
    image_storage_secret_access_key: str | None = None
    image_storage_path_template: str | None = None
    image_storage_public_base_url: str | None = None

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
    clear_set = {field for field in (clear_fields or []) if field in SECRET_FIELDS}

    for field in clear_set:
        if field == "fns_token":
            user_settings["fns_token"] = ""
        elif field == "image_storage_secret_access_key":
            image_storage["secret_access_key"] = ""

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

    if "image_mode" in payload and payload.get("image_mode") is not None:
        user_settings["image_mode"] = str(payload.get("image_mode") or "").strip() or DEFAULT_IMAGE_MODE

    for field in IMAGE_STORAGE_TEXT_FIELDS:
        if field not in payload:
            continue
        raw_value = payload.get(field)
        if raw_value is None:
            continue
        image_storage[field.removeprefix("image_storage_")] = str(raw_value).strip()

    user_settings["image_storage"] = image_storage
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


def build_admin_settings_payload() -> dict[str, Any]:
    settings = get_settings()
    runtime_values = load_runtime_config(settings.runtime_config_path)
    user_settings = runtime_values["user_settings"]
    image_storage = user_settings["image_storage"]
    runtime_overrides = [
        "auth.user.username",
        "auth.user.password_hash",
        "auth.session_secret_encrypted",
        *[
            f"user_settings.{key}"
            for key in sorted(user_settings.keys())
            if key != "image_storage"
        ],
        *[f"user_settings.image_storage.{key}" for key in sorted(image_storage.keys())],
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
        "runtime_overrides": runtime_overrides,
    }


def get_settings() -> Settings:
    runtime_config_path = get_runtime_config_path()
    runtime_values = load_runtime_config(runtime_config_path)
    auth_block = runtime_values["auth"]
    user_block = auth_block["user"]
    runtime_user_settings = runtime_values["user_settings"]
    image_storage = runtime_user_settings["image_storage"]

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
        image_mode=image_mode,
        image_storage_provider=provider,
        image_storage_endpoint=endpoint.rstrip("/") if endpoint else None,
        image_storage_region=region,
        image_storage_bucket=bucket,
        image_storage_access_key_id=access_key_id,
        image_storage_secret_access_key=secret_access_key,
        image_storage_path_template=path_template,
        image_storage_public_base_url=public_base_url.rstrip("/") if public_base_url else None,
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
        flat_user_settings = {key: raw_data.get(key) for key in FNS_FIELDS if key in raw_data}
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
    }


def _write_runtime_config(config_path: Path, data: dict[str, Any]) -> None:
    serialized = _serialize_runtime_config(data)
    config_path.write_text(json.dumps(serialized, ensure_ascii=False, indent=2), encoding="utf-8")


def _serialize_runtime_config(data: dict[str, Any]) -> dict[str, Any]:
    auth_block = data["auth"]
    user_settings = data["user_settings"]
    image_storage = user_settings["image_storage"]
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
