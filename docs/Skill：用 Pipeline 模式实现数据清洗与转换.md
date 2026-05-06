---
type: brick_note
brick_type: skill
status: draft
execution_mode: ai_executable
domain: backend-development
tags:
  - skill-brick
  - pipeline
  - data-processing
  - html
  - markdown
summary: 用 Pipeline（管线）模式把一个复杂的数据处理任务拆成多个独立步骤，每步只做一件事，组合成一条完整处理链
input:
  - 原始数据源（URL 或文件）
  - 每个步骤的处理逻辑
output:
  - 经过完整管线处理后的最终结果
source_wiki:
  - "[[project-wiki]]"
created: 2026-05-06
updated: 2026-05-06
last_tested:
usable: true
---

# Skill：用 Pipeline 模式实现数据清洗与转换

> [!abstract]
> 这张 Skill-brick 用来让开发者或 AI Agent 理解和使用 Pipeline（管线）模式，把一个复杂的数据处理任务拆成"抓取 → 提取 → 清洗 → 转换 → 格式化"的链式步骤，每步独立、可测试、可替换。

---

## 1 一句话用途

将一个复杂的数据处理任务拆成多个独立的、可组合的处理步骤，形成一条管线，数据从一端进入，经过每个步骤逐步处理，最终从另一端输出结果。

## 2 什么时候使用

> [!tip] 适用

- 数据需要经过多个处理阶段（如抓取、提取、清洗、转换、格式化）
- 每个阶段的处理逻辑不同，但数据格式可以在阶段之间传递
- 需要独立测试、替换或跳过某个阶段
- 处理流程可能扩展（如未来要加 AI 润色、加水印等步骤）

> [!warning] 不适用

- 只需要一步简单转换（如只调一个 API）——不需要管线
- 步骤之间有复杂的循环依赖——管线是单向的，不适合有回路的场景
- 数据量极大需要分布式处理——单机管线无法水平扩展

## 3 开始前需要什么

| 参数 | 必需 | 默认值 | AI 可自动发现 | 示例 / 说明 |
| --- | --- | --- | --- | --- |
| 数据源 | 是 | 无 | 否 | URL、文件路径、API 响应 |
| 管线步骤定义 | 是 | 无 | 否 | 每个步骤的输入/输出格式 |
| 数据结构定义 | 是 | 无 | 否 | 步骤之间传递的数据结构（dataclass） |
| 中间数据格式 | 是 | dict/dataclass | 是 | Python 项目通常用 dataclass |
| 项目管线文件 | 是 | `app/core/pipeline.py` | 是 | AI 可以 grep `Pipeline` 或 `pipeline` 定位 |

## 4 核心判断

| 判断点 | 选择规则 | 影响 |
| --- | --- | --- |
| 数据结构：dataclass vs dict | 步骤间传递的数据字段较多（>3 个）→ 用 dataclass，可以类型检查；字段少且不固定 → 用 dict | 影响步骤函数的参数签名 |
| 步骤粒度 | 每个步骤只做一件事。如果一步做了两件不同的事（如提取+清洗），拆成两步 | 影响管线的可测试性和可替换性 |
| 错误处理策略 | 某步失败是否终止整个管线？→ 通常终止并向上抛异常；某个步骤可选？→ 返回空结果并继续 | 影响步骤函数的返回值设计 |
| 是否需要中间结果 | 调试时需要看每步的输出 → 每步返回独立结果；不需要 → 只返回最终结果 | 影响管线编排函数的返回结构 |

> [!important] 关键原则
> Pipeline 模式的核心不是"一个类包揽所有事"，而是"数据在步骤之间流动"。每个步骤是一个函数或方法，接收上一步的输出，返回本步的结果。编排函数只负责调用顺序，不负责具体处理。

---

## 5 直接照做

### 5.1 准备

1. **明确管线的起点和终点**

```bash
grep -n "Pipeline\|run_pipeline\|def run_" app/core/pipeline.py
```

```text
起点：一个 URL（微信公众号文章链接）
终点：一个格式化好的 Markdown 文件 + 图片处理结果
```

2. **拆分步骤**

```text
URL
→ 第 1 步：fetch_html()        抓取原始 HTML
→ 第 2 步：extract_article()   从 HTML 中提取标题、作者、正文
→ 第 3 步：convert_to_md()     把正文 HTML 转成 Markdown
→ 第 4 步：format_markdown()   格式化 Markdown（去噪、排版）
→ 第 5 步：save_result()       保存到文件
```

3. **定义步骤间传递的数据结构**

```python
from dataclasses import dataclass

@dataclass
class ArticleData:
    title: str
    author: str
    account_name: str
    content_html: str
    original_url: str
```

### 5.2 执行：实现管线

#### 结构 1：类封装的管线（适合步骤间共享状态）

当多个步骤需要共享 HTTP Session 或配置时，用一个类封装共享状态，每个步骤是类的方法：

```python
class WeChatArticlePipeline:
    def __init__(self, timeout: int = 30) -> None:
        self.timeout = timeout
        self.session = requests.Session()           # 共享状态
        self.session.headers.update(self._build_headers())

    def validate_url(self, url: str) -> bool:
        """第 0 步：校验 URL 是否有效"""
        parsed = urlparse(url)
        return parsed.scheme in {'http', 'https'} and 'mp.weixin.qq.com' in parsed.netloc

    def fetch_html(self, url: str) -> str:
        """第 1 步：抓取原始 HTML"""
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        return response.text

    def extract_article(self, source_html: str, original_url: str) -> ArticleData:
        """第 2 步：从 HTML 提取结构化数据"""
        title = self._extract_first_match(source_html, [
            r'id="activity-name"[^>]*>\s*<span[^>]*>(.*?)</span>',
            r'<h1[^>]*>(.*?)</h1>',
            r'<title>(.*?)</title>',
        ]) or '未命名文章'
        author = self._extract_first_match(source_html, [
            r'id="js_author_name_text"[^>]*>(.*?)</span>',
        ]) or ''
        content_html = self._extract_content_html(source_html)
        content_html = self._clean_html(content_html)     # 清洗在提取中完成
        return ArticleData(
            title=title, author=author, account_name='',
            content_html=content_html, original_url=original_url,
        )
```

#### 结构 2：独立函数管线（适合步骤间无共享状态）

当步骤之间只传递数据，不需要共享状态时，用独立函数：

```python
def convert_article_to_markdown(article: ArticleData, output_dir: Path, timeout: int) -> tuple[str, int]:
    """第 3 步 + 第 4 步：HTML → Markdown → 格式化"""
    downloader = MarkdownImageDownloader(output_dir=output_dir, timeout=timeout)
    parser = HTMLToMarkdownParser(downloader)
    parser.feed(article.content_html)
    raw_markdown = parser.get_markdown()
    formatted, _ = format_markdown(raw_markdown, output_dir)
    return formatted, downloader.image_count
```

#### 结构 3：编排函数（把所有步骤串起来）

编排函数是管线的入口，它只负责按顺序调用各步骤，不负责具体处理逻辑：

```python
def run_pipeline(url: str, output_base_dir: Path, save_html: bool, timeout: int) -> dict:
    """管线编排：按顺序调用各步骤，返回完整结果"""
    # 第 0-1 步：校验 + 抓取
    pipeline = WeChatArticlePipeline(timeout=timeout)
    if not pipeline.validate_url(url):
        raise ValueError('无效的微信文章链接')
    source_html = pipeline.fetch_html(url)

    # 第 2 步：提取
    article = pipeline.extract_article(source_html, url)

    # 第 3-5 步：转换 + 格式化 + 保存（用 run_article_pipeline 编排子管线）
    return run_article_pipeline(
        article=article,
        output_base_dir=output_base_dir,
        save_html=save_html,
        timeout=timeout,
    )

def run_article_pipeline(article, output_base_dir, save_html, timeout) -> dict:
    """子管线编排：从 ArticleData 到最终文件"""
    # 准备输出路径
    output_dir, markdown_path, folder_name = build_output_paths(article.title, output_base_dir)

    # 转换
    raw_markdown, image_count, clean_html, image_summary = convert_article_to_markdown(
        article, output_dir, timeout
    )

    # 格式化
    formatted_markdown, format_summary = format_markdown(raw_markdown, output_dir)

    # 保存
    markdown_path.write_text(formatted_markdown, encoding='utf-8')

    return {
        'title': article.title,
        'markdown_file': str(markdown_path),
        'image_count': image_count,
        'format_summary': format_summary,
    }
```

> [!example]- 实际项目中的完整调用链
>
> ```text
> run_pipeline(url)                              # 顶层编排
>   ├── WeChatArticlePipeline.validate_url()     # 校验
>   ├── WeChatArticlePipeline.fetch_html()       # 抓取
>   ├── WeChatArticlePipeline.extract_article()  # 提取 + 清洗
>   └── run_article_pipeline()                   # 子管线编排
>         ├── build_output_paths()               # 准备路径
>         ├── convert_article_to_markdown()      # HTML → MD
>         │     ├── HTMLToMarkdownParser.feed()  # 解析 HTML
>         │     └── MarkdownImageDownloader      # 处理图片
>         ├── format_markdown()                  # 去噪 + 排版
>         └── write_text()                       # 保存文件
> ```

### 5.3 配置：添加新步骤

如果需要在管线中插入新步骤（如"AI 润色"），只需要：

1. 在编排函数中插入新的函数调用
2. 不需要修改已有的任何步骤函数

```python
# 在 run_article_pipeline 中，格式化之后、保存之前插入 AI 润色
formatted_markdown, format_summary = format_markdown(raw_markdown, output_dir)

# ↓ 新步骤：AI 润色（只需插入这一段）
if ai_enabled:
    formatted_markdown = apply_ai_polish(formatted_markdown, article)

markdown_path.write_text(formatted_markdown, encoding='utf-8')
```

### 5.4 验证

1. **单步测试**：每个步骤可以独立测试

```python
# 测试第 1 步：抓取
pipeline = WeChatArticlePipeline(timeout=30)
html = pipeline.fetch_html("https://mp.weixin.qq.com/s/test")
assert html, "HTML 不应为空"

# 测试第 2 步：提取
article = pipeline.extract_article(html, "https://mp.weixin.qq.com/s/test")
assert article.title, "标题不应为空"

# 测试第 3 步：转换
markdown, count = convert_article_to_markdown(article, output_dir, 30)
assert markdown, "Markdown 不应为空"
```

2. **端到端测试**：测试完整管线

```python
result = run_pipeline("https://mp.weixin.qq.com/s/test", Path("./output"), False, 30)
assert Path(result['markdown_file']).exists(), "Markdown 文件应该存在"
```

---

## 6 成功标准

| 检查项 | 检查方式 | 通过标准 |
| --- | --- | --- |
| 每个步骤是独立函数/方法 | 检查代码结构 | 每个步骤有独立的函数签名和返回值 |
| 步骤间通过数据结构传递 | 检查参数类型 | 使用 dataclass 或 dict，不直接传递全局变量 |
| 编排函数只负责顺序调用 | 检查编排函数 | 不包含具体处理逻辑，只有步骤函数的调用 |
| 新步骤可以在任意位置插入 | 在中间加一步 | 不需要修改已有步骤的代码 |
| 每个步骤可以独立测试 | 运行单步测试 | 给定输入，能返回预期输出 |
| 错误会从步骤向上传播 | 模拟某步失败 | 编排函数能捕获异常并报告出错的步骤 |

---

## 7 出错先查

| 现象 | 先检查 | 处理方向 |
| --- | --- | --- |
| 某步返回空结果 | 该步骤的输入数据是否正确格式 | 用 `print()` 或断点检查上一步的输出 |
| 管线中途报错 | 是哪一步报的错？看 traceback 的函数名 | 检查对应步骤函数的输入参数 |
| 最终结果缺少某些字段 | 数据结构定义是否完整 | 检查 dataclass 是否包含所有需要的字段 |
| 性能慢 | 是哪一步最慢？ | 在每个步骤前后加计时，定位瓶颈步骤 |
| 新步骤插入后管线出错 | 新步骤的输入/输出格式是否匹配上下步骤 | 检查新步骤接收和返回的数据类型 |

---

## 8 给 AI 的执行指令

> [!quote]- AI 执行指令
>
> ```text
> 请参考这张 Skill-brick，帮我用 Pipeline 模式实现一个数据处理任务。
>
> 执行规则：
> 1. 先阅读整篇 Skill-brick，确认 execution_mode 是 ai_executable。
> 2. 先明确：数据的起点是什么？终点是什么？中间需要几个步骤？
> 3. 画出步骤流程图（类似"URL → fetch → extract → convert → format → save"）。
> 4. 定义步骤间传递的数据结构（用 dataclass）。
> 5. 如果缺少关键参数（数据源、步骤定义），必须先问我。
> 6. 按以下顺序实现：
>    a. 先定义数据结构
>    b. 再实现每个步骤的函数
>    c. 最后写编排函数把步骤串起来
> 7. 实现完后，帮我生成每个步骤的独立测试用例。
> 8. 结束时按"成功标准"逐项验证，并告诉我每项是否通过。
>
> 本次环境 / 输入：
> - 项目路径：{{粘贴项目根目录路径}}
> - 数据源：{{粘贴数据源描述}}
> - 处理步骤：{{列出你想要的步骤}}
> - 输出格式：{{描述期望的最终输出}}
> ```

---

## 9 来源

相关 Wiki：
- [[project-wiki]]

外部参考：
- wechat-md-server 项目源码 `app/core/pipeline.py`（WeChatArticlePipeline 类、run_pipeline 函数、run_article_pipeline 函数）

---

**引用来源**：[[project-wiki]]、`app/core/pipeline.py`
