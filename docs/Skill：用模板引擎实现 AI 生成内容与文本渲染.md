---
type: brick_note
brick_type: skill
status: draft
execution_mode: ai_executable
domain: backend-development
tags:
  - skill-brick
  - template-engine
  - ai
  - python
summary: 实现一个轻量级模板渲染引擎，用 {{variable}} 占位符把 AI 生成的结构化变量（JSON）渲染到用户自定义的文本模板中
input:
  - 用户自定义的文本模板（含 {{variable}} 占位符）
  - AI 生成的结构化变量（dict）
output:
  - 渲染后的最终文本（占位符被替换为实际值）
source_wiki:
  - "[[project-wiki]]"
created: 2026-05-06
updated: 2026-05-06
last_tested:
usable: true
---

# Skill：用模板引擎实现 AI 生成内容与文本渲染

> [!abstract]
> 这张 Skill-brick 用来让开发者或 AI Agent 理解并实现一个轻量级模板渲染引擎：用正则表达式匹配 `{{variable}}` 占位符，把 AI 生成的 JSON 变量（摘要、标签等）渲染到用户自定义的 Markdown 模板中，生成最终的 Obsidian 笔记。

---

## 1 一句话用途

把"AI 生成变量 → 用户自定义模板 → 最终文本"这条链路打通，让用户可以通过修改模板来控制输出格式，不需要改代码。

## 2 什么时候使用

> [!tip] 适用

- AI 生成的是结构化数据（JSON），需要按照用户自定义的格式输出
- 用户希望能自己修改输出格式（如 Obsidian 的 frontmatter、body 模板）
- 一个 AI 调用生成多个变量，需要填充到同一个模板的不同位置

> [!warning] 不适用

- AI 直接输出最终文本，不需要中间变量——不需要模板引擎
- 输出格式固定不变——直接用 f-string 或字符串拼接就行
- 模板需要条件逻辑（if/else）或循环——需要 Jinja2 等完整模板引擎

## 3 开始前需要什么

| 参数 | 必需 | 默认值 | AI 可自动发现 | 示例 / 说明 |
| --- | --- | --- | --- | --- |
| 模板字符串 | 是 | 无 | 否 | 含 `{{variable}}` 占位符的文本 |
| 变量字典 | 是 | 无 | 否 | AI 生成的 JSON，如 `{"summary": "...", "tags": [...]}` |
| 占位符格式 | 否 | `{{variable}}` | 是 | AI 可以 grep `PLACEHOLDER_PATTERN` 定位 |
| 列表渲染格式 | 否 | `comma` | 是 | `comma`（逗号分隔）或 `json`（JSON 数组） |

## 4 核心判断

| 判断点 | 选择规则 | 影响 |
| --- | --- | --- |
| 占位符正则 | `{{\s*([a-zA-Z0-9_]+)\s*}}` 匹配 `{{name}}`、`{{ name }}` 等 | 影响模板中变量名的识别 |
| 缺失变量处理 | 变量不存在时返回空字符串（不报错） | 影响模板渲染的容错性 |
| 列表类型变量 | `tags` 等字段是 list，需要决定渲染方式：逗号分隔 vs JSON 数组 | 影响 `list_format` 参数 |
| AI 返回的 JSON 解析 | AI 可能返回 ` ```json ... ``` ` 包裹的内容 | 需要先清理 markdown 标记再解析 |

> [!important] 关键原则
> 模板引擎的核心是"分离数据与格式"：AI 只负责生成结构化数据（dict），用户通过模板控制输出格式。这样用户可以自由调整 frontmatter 字段顺序、body 结构，不需要改任何代码。

---

## 5 直接照做

### 5.1 准备

1. **确认模板格式和变量列表**

```text
用户模板示例（frontmatter）：
---
title: {{title}}
author: {{author}}
summary: {{summary}}
tags: {{tags}}
---

用户模板示例（body）：
> [!summary] 一句话总结
> {{summary}}

> [!tip] 我的理解
> {{my_understand}}

{{body_polish}}
```

2. **定位模板渲染函数**

```bash
grep -n "render_template\|PLACEHOLDER_PATTERN" app/ai_polish.py
```

### 5.2 执行

#### 位置 1：定义占位符正则（app/ai_polish.py）

```python
import re

# 匹配 {{variable}} 或 {{ variable }}
PLACEHOLDER_PATTERN = re.compile(r"{{\s*([a-zA-Z0-9_]+)\s*}}")
```

#### 位置 2：实现渲染函数（app/ai_polish.py）

```python
import json
from typing import Any

def render_template(template: str, variables: dict[str, Any], *, list_format: str = "comma") -> str:
    """
    把模板中的 {{variable}} 替换为变量值。
    - 字符串：直接替换
    - 列表：逗号分隔 或 JSON 数组（由 list_format 控制）
    - 缺失变量：替换为空字符串
    """
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = variables.get(key, "")
        if isinstance(value, list):
            if list_format == "json":
                return json.dumps([str(item) for item in value], ensure_ascii=False)
            return ", ".join(str(item) for item in value)
        return str(value or "")

    return PLACEHOLDER_PATTERN.sub(replace, template or "")
```

#### 位置 3：实现 AI JSON 响应解析（app/ai_polish.py）

AI 返回的内容可能被 markdown 代码块包裹，需要清理后再解析：

```python
def _parse_json_response(text: str) -> dict[str, Any]:
    """解析 AI 返回的 JSON，处理可能的 markdown 包裹"""
    cleaned = text.strip()

    # 去掉 ```json ... ``` 包裹
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        # 尝试找到 JSON 对象的边界
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise RuntimeError("AI 返回不是有效 JSON")
        parsed = json.loads(cleaned[start : end + 1])

    if not isinstance(parsed, dict):
        raise RuntimeError("AI 返回 JSON 必须是对象")
    return parsed
```

#### 位置 4：实现完整的渲染流程（app/ai_polish.py）

把"AI 生成变量 → 模板渲染"串起来：

```python
def apply_ai_polish_to_markdown(
    *,
    markdown_path: Path,
    metadata: dict[str, Any],
    interpreter_prompt: str,
    frontmatter_template: str,
    body_template: str,
    context_template: str = "{{content}}",
    # AI 调用参数...
) -> dict[str, Any]:
    # 1. 读取原始 Markdown
    original_content = markdown_path.read_text(encoding="utf-8")

    # 2. 准备基础变量（不需要 AI 生成的）
    base_variables = {
        "title": metadata.get("title", ""),
        "author": metadata.get("author", ""),
        "url": metadata.get("url", ""),
        "date": metadata.get("date", ""),
        "content": original_content.strip(),
        "summary": "",   # AI 填充
        "tags": "",      # AI 填充
    }

    # 3. 调用 AI 生成变量
    rendered_context = render_template(context_template, base_variables)
    # ... 调用 AI API，得到 interpreted 变量 ...
    interpreted = request_interpreter_variables(prompt=..., ...)

    # 4. 合并变量
    variables = {**base_variables, **interpreted}

    # 5. 渲染模板
    frontmatter = render_template(frontmatter_template, variables, list_format="json").strip()
    body = render_template(body_template, variables).strip()

    # 6. 组装最终 Markdown
    sections = [section for section in (frontmatter, body) if section]
    # 如果 body 模板中没有 {{content}}，追加原始正文
    if "{{content}}" not in body_template:
        sections.append(variables["content"])

    markdown_path.write_text("\n\n".join(sections).strip() + "\n", encoding="utf-8")
    return {"status": "success", "template_applied": True}
```

### 5.3 验证

```python
# 测试基本渲染
template = "标题：{{title}}，作者：{{author}}"
result = render_template(template, {"title": "测试", "author": "张三"})
assert result == "标题：测试，作者：张三"

# 测试列表渲染
template = "tags: {{tags}}"
result = render_template(template, {"tags": ["AI", "Python", "Obsidian"]})
assert result == "tags: AI, Python, Obsidian"

# 测试 JSON 格式列表
result = render_template(template, {"tags": ["AI", "Python"]}, list_format="json")
assert result == 'tags: ["AI", "Python"]'

# 测试缺失变量
result = render_template("标题：{{title}}", {"author": "张三"})
assert result == "标题："

# 测试带空格的占位符
result = render_template("标题：{{ title }}", {"title": "测试"})
assert result == "标题：测试"

print("所有测试通过")
```

---

## 6 成功标准

| 检查项 | 检查方式 | 通过标准 |
| --- | --- | --- |
| 占位符正则定义 | `grep "PLACEHOLDER_PATTERN" app/ai_polish.py` | 正则存在，能匹配 `{{name}}` |
| 渲染函数存在 | `grep "def render_template" app/ai_polish.py` | 函数存在，参数包含 template、variables |
| 列表渲染支持 | 检查 render_template | 支持 `list_format` 参数 |
| JSON 解析函数存在 | `grep "_parse_json_response" app/ai_polish.py` | 函数存在，能处理 markdown 包裹 |
| 完整渲染流程存在 | `grep "apply_ai_polish" app/ai_polish.py` | 函数存在，包含变量合并和模板渲染 |
| 缺失变量不报错 | 渲染不存在的变量 | 返回空字符串 |

---

## 7 出错先查

| 现象 | 先检查 | 处理方向 |
| --- | --- | --- |
| 占位符没有被替换 | 正则是否匹配占位符格式 | 检查是否有拼写错误或多余空格 |
| AI 返回 JSON 解析失败 | AI 返回的内容是否包含额外文字 | 检查 `_parse_json_response` 的清理逻辑 |
| 列表渲染为字符串 | `tags` 变量是否是 list 类型 | 检查 AI 返回的 tags 是否被正确解析为 list |
| 模板渲染后多出空行 | 变量值为空字符串时的换行处理 | 在模板中用条件渲染或渲染后清理连续空行 |
| frontmatter 格式错误 | 模板中 YAML 特殊字符是否被转义 | 检查变量值中是否包含 `:` 或 `---` |

---

## 8 给 AI 的执行指令

> [!quote]- AI 执行指令
>
> ```text
> 请参考这张 Skill-brick，帮我实现一个轻量级模板渲染引擎。
>
> 执行规则：
> 1. 先阅读整篇 Skill-brick，确认 execution_mode 是 ai_executable。
> 2. 先明确：模板格式是什么？变量有哪些？哪些是列表类型？
> 3. 按以下顺序实现：
>    a. 定义占位符正则
>    b. 实现 render_template 函数
>    c. 实现 JSON 解析函数（如果需要处理 AI 返回）
>    d. 实现完整渲染流程
> 4. 实现完后，帮我生成测试用例验证渲染结果。
> 5. 结束时按"成功标准"逐项验证。
>
> 本次环境 / 输入：
> - 项目路径：{{粘贴项目根目录路径}}
> - 模板格式：{{描述模板中的占位符格式}}
> - 变量列表：{{列出所有变量名和类型}}
> ```

---

## 9 来源

相关 Wiki：
- [[project-wiki]]

外部参考：
- wechat-md-server 项目源码 `app/ai_polish.py`（render_template 函数、apply_ai_polish_to_markdown 函数）

---

**引用来源**：[[project-wiki]]、`app/ai_polish.py`
