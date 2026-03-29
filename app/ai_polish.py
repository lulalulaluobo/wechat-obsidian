from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from app.ai_adapters import extract_completion_preview, extract_completion_text, request_ai_completion, validate_provider_model


PLACEHOLDER_PATTERN = re.compile(r"{{\s*([a-zA-Z0-9_]+)\s*}}")
PROMPT_PLACEHOLDER_PATTERN = re.compile(r'{{\s*"([^"]+)"\s*}}')
ESCAPED_PROMPT_PLACEHOLDER_PATTERN = re.compile(r'{{\s*\\"([^"]+)\\"\s*}}')


def render_template(template: str, variables: dict[str, Any], *, list_format: str = "comma") -> str:
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = variables.get(key, "")
        if isinstance(value, list):
            if list_format == "json":
                return json.dumps([str(item) for item in value], ensure_ascii=False)
            return ", ".join(str(item) for item in value)
        return str(value or "")

    return PLACEHOLDER_PATTERN.sub(replace, template or "")


def request_interpreter_variables(
    *,
    provider: dict[str, Any] | None = None,
    model: dict[str, Any] | None = None,
    ai_base_url: str | None = None,
    ai_api_key: str | None = None,
    ai_model: str | None = None,
    prompt: str,
    http_session=None,
    timeout: int = 60,
) -> dict[str, Any]:
    resolved_provider = provider or {
        "id": "legacy-openai-compatible",
        "type": "openai_compatible",
        "display_name": "OpenAI Compatible",
        "base_url": str(ai_base_url or "").strip(),
        "api_key": str(ai_api_key or "").strip(),
        "enabled": True,
        "built_in": False,
    }
    resolved_model = model or {
        "id": "legacy-model",
        "provider_id": str(resolved_provider.get("id") or "legacy-openai-compatible"),
        "display_name": str(ai_model or "").strip(),
        "model_id": str(ai_model or "").strip(),
        "enabled": True,
    }
    validate_provider_model(resolved_provider, resolved_model)
    payload = request_ai_completion(
        provider=resolved_provider,
        model=resolved_model,
        messages=[
            {"role": "system", "content": "你是一个结构化笔记解释器，只返回 JSON 对象。"},
            {"role": "user", "content": prompt},
        ],
        timeout=timeout,
        http_session=http_session,
        temperature=0.2,
    )
    content_text = extract_completion_preview(payload, provider_type=str(resolved_provider.get("type") or "openai_compatible"))
    if not content_text:
        raise RuntimeError("AI 返回内容为空")
    return _parse_json_response(content_text)


def request_polished_content(
    *,
    provider: dict[str, Any] | None = None,
    model: dict[str, Any] | None = None,
    ai_base_url: str | None = None,
    ai_api_key: str | None = None,
    ai_model: str | None = None,
    metadata: dict[str, Any],
    context: str,
    polish_prompt: str,
    http_session=None,
    timeout: int = 60,
) -> str:
    resolved_provider = provider or {
        "id": "legacy-openai-compatible",
        "type": "openai_compatible",
        "display_name": "OpenAI Compatible",
        "base_url": str(ai_base_url or "").strip(),
        "api_key": str(ai_api_key or "").strip(),
        "enabled": True,
        "built_in": False,
    }
    resolved_model = model or {
        "id": "legacy-model",
        "provider_id": str(resolved_provider.get("id") or "legacy-openai-compatible"),
        "display_name": str(ai_model or "").strip(),
        "model_id": str(ai_model or "").strip(),
        "enabled": True,
    }
    validate_provider_model(resolved_provider, resolved_model)
    prompt = "\n".join(
        [
            "请根据以下元数据和正文，输出润色后的 Obsidian Markdown 正文。",
            "只返回正文 Markdown，不要返回 JSON，不要额外解释。",
            "",
            "元数据：",
            f"- title: {metadata.get('title') or ''}",
            f"- author: {metadata.get('author') or ''}",
            f"- url: {metadata.get('url') or ''}",
            f"- date: {metadata.get('date') or ''}",
            "",
            "润色要求：",
            str(polish_prompt or "").strip(),
            "",
            "正文：",
            context.strip(),
        ]
    ).strip()
    payload = request_ai_completion(
        provider=resolved_provider,
        model=resolved_model,
        messages=[
            {"role": "system", "content": "你是一个 Obsidian Markdown 润色助手，只返回正文 Markdown。"},
            {"role": "user", "content": prompt},
        ],
        timeout=timeout,
        http_session=http_session,
        temperature=0.2,
    )
    content_text = extract_completion_text(payload, provider_type=str(resolved_provider.get("type") or "openai_compatible")).strip()
    if not content_text:
        raise RuntimeError("AI 返回内容为空")
    if content_text.startswith("```"):
        content_text = re.sub(r"^```(?:markdown|md|text)?\s*", "", content_text, flags=re.IGNORECASE)
        content_text = re.sub(r"\s*```$", "", content_text)
    return content_text.strip()


def build_prompt_from_variable_prompts(
    variable_prompts: dict[str, str],
    metadata: dict[str, Any],
    context: str,
) -> str:
    lines = [
        "你是一个 Obsidian 笔记解释器。请基于提供的元数据和上下文，一次性返回 JSON 对象。",
        "不要输出 Markdown，不要额外解释，只返回 JSON。",
        "",
        "元数据：",
        f"- title: {metadata.get('title') or ''}",
        f"- author: {metadata.get('author') or ''}",
        f"- url: {metadata.get('url') or ''}",
        f"- date: {metadata.get('date') or ''}",
        "",
        "需要输出的 JSON 字段：",
    ]
    for key, prompt in variable_prompts.items():
        lines.append(f'- {key}: {prompt}')
    lines.extend(
        [
            "",
            "上下文：",
            context.strip(),
        ]
    )
    return "\n".join(lines).strip()


def extract_prompt_variables_from_templates(
    *,
    frontmatter_template: str,
    body_template: str,
) -> tuple[str, str, dict[str, str]]:
    variable_prompts: dict[str, str] = {}

    def normalize_prompt_text(raw_prompt: str) -> str:
        return str(raw_prompt or "").strip()

    normalized_frontmatter_lines: list[str] = []
    for line in str(frontmatter_template or "").splitlines():
        if ":" not in line:
            normalized_frontmatter_lines.append(line)
            continue
        key_part, value_part = line.split(":", 1)
        field_name = key_part.strip()
        prompt_text = _extract_prompt_placeholder(value_part.strip())
        if field_name and prompt_text:
            variable_prompts[field_name] = normalize_prompt_text(prompt_text)
            normalized_frontmatter_lines.append(f"{key_part}: {{{{{field_name}}}}}")
        else:
            normalized_frontmatter_lines.append(line)

    block_index = 0

    def replace_body_prompt(match: re.Match[str]) -> str:
        nonlocal block_index
        block_index += 1
        variable_name = f"clipper_block_{block_index}"
        variable_prompts[variable_name] = normalize_prompt_text(match.group(1))
        return f"{{{{{variable_name}}}}}"

    normalized_body = PROMPT_PLACEHOLDER_PATTERN.sub(replace_body_prompt, str(body_template or ""))
    normalized_body = ESCAPED_PROMPT_PLACEHOLDER_PATTERN.sub(replace_body_prompt, normalized_body)

    return "\n".join(normalized_frontmatter_lines), normalized_body, variable_prompts


def apply_ai_polish_to_markdown(
    *,
    markdown_path: Path,
    metadata: dict[str, Any],
    provider: dict[str, Any] | None = None,
    model: dict[str, Any] | None = None,
    ai_base_url: str | None = None,
    ai_api_key: str | None = None,
    ai_model: str | None = None,
    interpreter_prompt: str,
    frontmatter_template: str,
    body_template: str,
    context_template: str = "{{content}}",
    allow_body_polish: bool,
    enable_content_polish: bool = False,
    content_polish_prompt: str = "",
    http_session=None,
    timeout: int = 60,
) -> dict[str, Any]:
    original_content = markdown_path.read_text(encoding="utf-8")
    normalized_frontmatter_template, normalized_body_template, extracted_variable_prompts = extract_prompt_variables_from_templates(
        frontmatter_template=frontmatter_template,
        body_template=body_template,
    )
    base_variables = {
        "title": str(metadata.get("title") or ""),
        "author": str(metadata.get("author") or ""),
        "url": str(metadata.get("url") or ""),
        "date": str(metadata.get("date") or datetime.now().strftime("%Y-%m-%d")),
        "content": original_content.strip(),
        "summary": "",
        "tags": "",
        "my_understand": "",
        "body_polish": "",
        "content_polished": "",
    }
    rendered_context = render_template(context_template, base_variables).strip() or base_variables["content"]
    prompt = _build_interpreter_prompt(
        interpreter_prompt=interpreter_prompt,
        template_variable_prompts=extracted_variable_prompts,
        metadata=base_variables,
        context=rendered_context,
    )
    interpreted = request_interpreter_variables(
        provider=provider,
        model=model,
        ai_base_url=ai_base_url,
        ai_api_key=ai_api_key,
        ai_model=ai_model,
        prompt=prompt,
        http_session=http_session,
        timeout=timeout,
    )
    normalized = _normalize_interpreted_variables(interpreted, allow_body_polish=allow_body_polish)
    polished_content = ""
    content_polish_degraded = False
    if enable_content_polish and str(content_polish_prompt or "").strip():
        try:
            polished_content = request_polished_content(
                provider=provider,
                model=model,
                ai_base_url=ai_base_url,
                ai_api_key=ai_api_key,
                ai_model=ai_model,
                metadata=base_variables,
                context=rendered_context,
                polish_prompt=str(content_polish_prompt or "").strip(),
                http_session=http_session,
                timeout=timeout,
            )
        except Exception:
            content_polish_degraded = True
    final_content = original_content.strip()
    if enable_content_polish and polished_content:
        final_content = polished_content
    variables = {
        **base_variables,
        **normalized,
        "content": final_content,
        "content_raw": original_content.strip(),
        "content_polished": polished_content,
    }
    frontmatter = render_template(
        normalized_frontmatter_template,
        variables,
        list_format="json",
    ).strip()
    body = render_template(normalized_body_template, variables).strip()
    sections = [section for section in (frontmatter, body) if section]
    if "{{content}}" not in (normalized_body_template or "") and "{{content_polished}}" not in (normalized_body_template or ""):
        sections.append(final_content)
    markdown_path.write_text("\n\n".join(sections).strip() + "\n", encoding="utf-8")
    return {
        "enabled": True,
        "status": "success",
        "model": str((model or {}).get("model_id") or ai_model or ""),
        "template_applied": True,
        "summary": normalized["summary"],
        "tags": normalized["tags"],
        "content_polished": bool(enable_content_polish and polished_content),
        "message": "AI 润色已应用（正文润色已降级）" if content_polish_degraded else "AI 润色已应用",
    }


def _build_interpreter_prompt(
    *,
    interpreter_prompt: str,
    template_variable_prompts: dict[str, str],
    metadata: dict[str, Any],
    context: str,
) -> str:
    parsed = _try_parse_prompt_mapping(interpreter_prompt)
    merged_prompts = {**template_variable_prompts}
    if parsed:
        merged_prompts.update(parsed)
    if not merged_prompts:
        return render_template(
            interpreter_prompt,
            {
                **metadata,
                "content": context,
            },
        )
    return build_prompt_from_variable_prompts(merged_prompts, metadata=metadata, context=context)


def _try_parse_prompt_mapping(interpreter_prompt: str) -> dict[str, str] | None:
    text = (interpreter_prompt or "").strip()
    if not text.startswith("{"):
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict):
        return None
    normalized: dict[str, str] = {}
    for key, value in parsed.items():
        normalized_key = str(key).strip()
        if not normalized_key:
            continue
        normalized[normalized_key] = str(value or "").strip()
    return normalized or None


def _extract_prompt_placeholder(value: str) -> str | None:
    for pattern in (PROMPT_PLACEHOLDER_PATTERN, ESCAPED_PROMPT_PLACEHOLDER_PATTERN):
        match = pattern.fullmatch(str(value or "").strip())
        if match:
            return str(match.group(1) or "").strip()
    return None


def _parse_json_response(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise RuntimeError("AI 返回不是有效 JSON")
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as error:
            raise RuntimeError("AI 返回不是有效 JSON") from error
    if not isinstance(parsed, dict):
        raise RuntimeError("AI 返回 JSON 必须是对象")
    return parsed


def _normalize_interpreted_variables(payload: dict[str, Any], *, allow_body_polish: bool) -> dict[str, Any]:
    tags_value = payload.get("tags")
    tags: list[str]
    if isinstance(tags_value, list):
        tags = [str(item).strip() for item in tags_value if str(item).strip()]
    elif isinstance(tags_value, str):
        tags = [part.strip() for part in re.split(r"[,\n]", tags_value) if part.strip()]
    else:
        tags = []
    deduped_tags: list[str] = []
    seen: set[str] = set()
    for tag in tags:
        if tag in seen:
            continue
        seen.add(tag)
        deduped_tags.append(tag)
    normalized = {
        "summary": str(payload.get("summary") or "").strip(),
        "tags": deduped_tags,
        "my_understand": str(payload.get("my_understand") or "").strip(),
        "body_polish": str(payload.get("body_polish") or "").strip() if allow_body_polish else "",
        "content_polished": str(payload.get("content_polished") or "").strip(),
    }
    for key, value in payload.items():
        if key in normalized:
            continue
        normalized[str(key)] = value
    return normalized
