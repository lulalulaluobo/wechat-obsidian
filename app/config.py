from __future__ import annotations

import os
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Settings:
    default_output_dir: Path
    default_r2_config_path: Path
    runtime_config_path: Path
    default_timeout: int = 30
    access_token: str | None = None
    fns_base_url: str | None = None
    fns_token: str | None = None
    fns_vault: str | None = None
    fns_target_dir: str = "00_Inbox/微信公众号"

    @property
    def fns_enabled(self) -> bool:
        return bool(self.fns_base_url and self.fns_token and self.fns_vault)


RUNTIME_CONFIG_FIELDS = {
    "fns_base_url",
    "fns_token",
    "fns_vault",
    "fns_target_dir",
    "access_token",
}
SECRET_FIELDS = {"fns_token", "access_token"}


def get_runtime_config_path() -> Path:
    configured = os.environ.get("WECHAT_MD_RUNTIME_CONFIG_PATH")
    if configured:
        return Path(configured).resolve()
    return (Path(__file__).resolve().parents[1] / "data" / "runtime-config.json").resolve()


def load_runtime_config(path: Path | None = None) -> dict[str, Any]:
    config_path = path or get_runtime_config_path()
    if not config_path.exists():
        return {}
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RuntimeError(f"运行时配置文件不是有效 JSON: {config_path}") from error
    if not isinstance(data, dict):
        raise RuntimeError(f"运行时配置文件结构无效: {config_path}")
    return {key: value for key, value in data.items() if key in RUNTIME_CONFIG_FIELDS}


def save_runtime_config(payload: dict[str, Any], clear_fields: list[str] | None = None) -> dict[str, Any]:
    config_path = get_runtime_config_path()
    current = load_runtime_config(config_path)
    updated = dict(current)
    clear_set = {field for field in (clear_fields or []) if field in SECRET_FIELDS}

    for field in clear_set:
        updated.pop(field, None)

    for field in RUNTIME_CONFIG_FIELDS:
        if field not in payload:
            continue
        raw_value = payload.get(field)
        if raw_value is None:
            continue
        value = str(raw_value).strip()
        if field in SECRET_FIELDS:
            if value:
                updated[field] = value
            continue
        if value:
            updated[field] = value
        else:
            updated.pop(field, None)

    _validate_runtime_config(updated)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(updated, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return updated


def build_admin_settings_payload() -> dict[str, Any]:
    settings = get_settings()
    runtime_values = load_runtime_config(settings.runtime_config_path)
    return {
        "runtime_config_path": str(settings.runtime_config_path),
        "fns_base_url": settings.fns_base_url or "",
        "fns_vault": settings.fns_vault or "",
        "fns_target_dir": settings.fns_target_dir or "00_Inbox/微信公众号",
        "fns_token_configured": bool(settings.fns_token),
        "fns_token_masked": _mask_secret(settings.fns_token),
        "access_token_configured": bool(settings.access_token),
        "access_token_masked": _mask_secret(settings.access_token),
        "runtime_overrides": sorted(runtime_values.keys()),
    }


def get_settings() -> Settings:
    runtime_config_path = get_runtime_config_path()
    runtime_values = load_runtime_config(runtime_config_path)
    output_dir = Path(
        os.environ.get("WECHAT_MD_DEFAULT_OUTPUT_DIR", r"D:\obsidian\00_Inbox")
    ).resolve()
    r2_config_path = Path(
        os.environ.get(
            "WECHAT_MD_R2_CONFIG_PATH",
            r"D:\obsidian\.obsidian\plugins\image-upload-toolkit\data.json",
        )
    ).resolve()
    access_token = (runtime_values.get("access_token") or os.environ.get("WECHAT_MD_ACCESS_TOKEN") or "").strip() or None
    fns_base_url = (
        runtime_values.get("fns_base_url") or os.environ.get("WECHAT_MD_FNS_BASE_URL") or ""
    ).strip() or None
    fns_token = (
        runtime_values.get("fns_token") or os.environ.get("WECHAT_MD_FNS_TOKEN") or ""
    ).strip() or None
    fns_vault = (
        runtime_values.get("fns_vault") or os.environ.get("WECHAT_MD_FNS_VAULT") or ""
    ).strip() or None
    fns_target_dir = (
        runtime_values.get("fns_target_dir")
        or os.environ.get("WECHAT_MD_FNS_TARGET_DIR", "00_Inbox/微信公众号")
        or "00_Inbox/微信公众号"
    ).strip() or "00_Inbox/微信公众号"
    return Settings(
        default_output_dir=output_dir,
        default_r2_config_path=r2_config_path,
        runtime_config_path=runtime_config_path,
        access_token=access_token,
        fns_base_url=fns_base_url.rstrip("/") if fns_base_url else None,
        fns_token=fns_token,
        fns_vault=fns_vault,
        fns_target_dir=fns_target_dir.strip("/\\"),
    )


def _mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}...{value[-4:]}"


def _validate_runtime_config(data: dict[str, Any]) -> None:
    base_url = str(data.get("fns_base_url") or "").strip()
    if base_url and not base_url.startswith(("http://", "https://")):
        raise ValueError("FNS 基础地址必须以 http:// 或 https:// 开头")
