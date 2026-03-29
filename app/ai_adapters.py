from __future__ import annotations

import json
from typing import Any

import requests


def request_ai_completion(
    *,
    provider: dict[str, Any],
    model: dict[str, Any],
    messages: list[dict[str, Any]],
    timeout: int,
    http_session=None,
    temperature: float = 0,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    provider_type = str(provider.get("type") or "").strip()
    if provider_type in {"openai_compatible", "openrouter", "custom"}:
        return _request_openai_compatible(
            provider=provider,
            model=model,
            messages=messages,
            timeout=timeout,
            http_session=http_session,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    if provider_type == "anthropic":
        return _request_anthropic(
            provider=provider,
            model=model,
            messages=messages,
            timeout=timeout,
            http_session=http_session,
            max_tokens=max_tokens,
        )
    if provider_type == "gemini":
        return _request_gemini(
            provider=provider,
            model=model,
            messages=messages,
            timeout=timeout,
            http_session=http_session,
        )
    if provider_type == "ollama":
        return _request_ollama(
            provider=provider,
            model=model,
            messages=messages,
            timeout=timeout,
            http_session=http_session,
            temperature=temperature,
        )
    raise RuntimeError(f"暂不支持的 AI provider 类型: {provider_type}")


def extract_completion_text(payload: dict[str, Any], *, provider_type: str) -> str:
    if provider_type in {"openai_compatible", "openrouter", "custom"}:
        return _extract_openai_content(payload)
    if provider_type == "anthropic":
        content = payload.get("content")
        if isinstance(content, list):
            return "".join(
                str(item.get("text") or "")
                for item in content
                if isinstance(item, dict)
            ).strip()
        return ""
    if provider_type == "gemini":
        candidates = payload.get("candidates")
        if isinstance(candidates, list) and candidates:
            first = candidates[0]
            if isinstance(first, dict):
                parts = (((first.get("content") or {}).get("parts")) if isinstance(first.get("content"), dict) else None)
                if isinstance(parts, list):
                    return "".join(str(part.get("text") or "") for part in parts if isinstance(part, dict)).strip()
        return ""
    if provider_type == "ollama":
        message = payload.get("message")
        if isinstance(message, dict):
            return str(message.get("content") or "").strip()
        return ""
    return json.dumps(payload, ensure_ascii=False)


def extract_completion_preview(payload: dict[str, Any], *, provider_type: str) -> str:
    return extract_completion_text(payload, provider_type=provider_type)[:400]


def validate_provider_model(provider: dict[str, Any], model: dict[str, Any]) -> None:
    provider_type = str(provider.get("type") or "").strip()
    if provider_type not in {"openai_compatible", "openrouter", "anthropic", "gemini", "ollama", "custom"}:
        raise ValueError("AI provider 类型无效")
    if not str(provider.get("id") or "").strip():
        raise ValueError("AI provider id 不能为空")
    if not str(provider.get("display_name") or "").strip():
        raise ValueError("AI provider 名称不能为空")
    if not str(model.get("id") or "").strip():
        raise ValueError("AI model id 不能为空")
    if not str(model.get("model_id") or "").strip():
        raise ValueError("AI 模型标识不能为空")
    if provider_type in {"openai_compatible", "openrouter", "ollama", "custom"}:
        base_url = str(provider.get("base_url") or "").strip()
        if not base_url or not base_url.startswith(("http://", "https://")):
            raise ValueError("AI provider Base URL 必须以 http:// 或 https:// 开头")
    if provider_type in {"openrouter", "anthropic", "gemini"} and not str(provider.get("api_key") or "").strip():
        raise ValueError("当前 provider 需要 API Key")


def _request_openai_compatible(
    *,
    provider: dict[str, Any],
    model: dict[str, Any],
    messages: list[dict[str, Any]],
    timeout: int,
    http_session=None,
    temperature: float = 0,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    session = http_session or requests.Session()
    base_url = str(provider.get("base_url") or "").rstrip("/")
    headers = {"Content-Type": "application/json"}
    api_key = str(provider.get("api_key") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    payload: dict[str, Any] = {
        "model": str(model.get("model_id") or ""),
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    response = session.post(
        f"{base_url}/chat/completions",
        headers=headers,
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def _request_anthropic(
    *,
    provider: dict[str, Any],
    model: dict[str, Any],
    messages: list[dict[str, Any]],
    timeout: int,
    http_session=None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    session = http_session or requests.Session()
    base_url = str(provider.get("base_url") or "https://api.anthropic.com/v1").rstrip("/")
    api_key = str(provider.get("api_key") or "").strip()
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    system_messages = [msg.get("content") for msg in messages if msg.get("role") == "system"]
    user_messages = [msg for msg in messages if msg.get("role") != "system"]
    payload: dict[str, Any] = {
        "model": str(model.get("model_id") or ""),
        "messages": user_messages,
        "max_tokens": max_tokens or 256,
    }
    if system_messages:
        payload["system"] = "\n".join(str(item or "") for item in system_messages).strip()
    response = session.post(
        f"{base_url}/messages",
        headers=headers,
        json=payload,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def _request_gemini(
    *,
    provider: dict[str, Any],
    model: dict[str, Any],
    messages: list[dict[str, Any]],
    timeout: int,
    http_session=None,
) -> dict[str, Any]:
    session = http_session or requests.Session()
    base_url = str(provider.get("base_url") or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
    api_key = str(provider.get("api_key") or "").strip()
    parts: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role") or "")
        content = str(message.get("content") or "")
        if not content:
            continue
        prefix = "System" if role == "system" else "User"
        parts.append({"text": f"{prefix}: {content}"})
    response = session.post(
        f"{base_url}/models/{model.get('model_id')}:generateContent",
        params={"key": api_key},
        json={"contents": [{"parts": parts}]},
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def _request_ollama(
    *,
    provider: dict[str, Any],
    model: dict[str, Any],
    messages: list[dict[str, Any]],
    timeout: int,
    http_session=None,
    temperature: float = 0,
) -> dict[str, Any]:
    session = http_session or requests.Session()
    base_url = str(provider.get("base_url") or "http://127.0.0.1:11434").rstrip("/")
    response = session.post(
        f"{base_url}/api/chat",
        json={
            "model": str(model.get("model_id") or ""),
            "messages": messages,
            "stream": False,
            "options": {"temperature": temperature},
        },
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def _extract_openai_content(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        return json.dumps(payload, ensure_ascii=False)
    first = choices[0]
    if not isinstance(first, dict):
        return json.dumps(payload, ensure_ascii=False)
    message = first.get("message")
    if not isinstance(message, dict):
        return json.dumps(payload, ensure_ascii=False)
    content = message.get("content")
    if isinstance(content, list):
        return "".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict)
        ).strip()
    return str(content or "").strip()
