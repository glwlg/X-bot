# Article Publisher 阶段化重构设计

## 概述

将 `article_publisher` skill 从单体流程拆分为 4 个可组合阶段，支持 Ikaros 自主完成全流程并在每个阶段自动验证输出质量。

## 目标

- **阶段拆分**：搜索、写作、配图、发布四个独立阶段
- **可组合性**：支持从任意阶段开始，支持外部文件输入
- **自主验证**：Ikaros 自动验证每个阶段输出，无需用户交互
- **向后兼容**：保留现有 CLI 参数和调用方式

## 详细设计文档

| 模块 | 设计文档 |
|------|---------|
| 编排器 | [orchestrator-design.md](./orchestrator-design.md) |
| 搜索阶段 | [search-stage-design.md](./search-stage-design.md) |
| 写作阶段 | [write-stage-design.md](./write-stage-design.md) |
| 配图阶段 | [illustrate-stage-design.md](./illustrate-stage-design.md) |
| 发布阶段 | [publish-stage-design.md](./publish-stage-design.md) |
| article.py | [article-utils-design.md](./article-utils-design.md) |
| wechat.py | [wechat-utils-design.md](./wechat-utils-design.md) |
| xiaohongshu.py | [xiaohongshu-utils-design.md](./xiaohongshu-utils-design.md) |

## 目录结构

```
article_publisher/
├── SKILL.md                      # Skill 元数据 + 文档
├── scripts/
│   ├── execute.py                # 主入口：CLI 解析 + 子命令路由 + 编排逻辑
│   ├── stages/
│   │   ├── __init__.py
│   │   ├── search.py             # 搜索阶段
│   │   ├── write.py              # 写作阶段
│   │   ├── illustrate.py         # 配图阶段
│   │   └── publish.py            # 发布阶段
│   └── utils/
│       ├── __init__.py
│       ├── article.py            # ArticleData 类、JSON 解析/验证
│       ├── wechat.py             # WeChatPublisher 类
│       └── xiaohongshu.py        # 小红书发布逻辑
```

## 数据流

```
[search] ──research.json──► [write] ──article.json──► [illustrate] ──article_with_images.json──► [publish]
```

## 输出文件

| 阶段 | 输出文件 | 默认路径 |
|------|---------|---------|
| search | `research.json` | `~/.ikaros/articles/{topic_slug}/research.json` |
| write | `article.json` | `~/.ikaros/articles/{topic_slug}/article.json` |
| illustrate | `article_with_images.json` | `~/.ikaros/articles/{topic_slug}/article_with_images.json` |
| publish | `publish_result.json` | `~/.ikaros/articles/{topic_slug}/publish_result.json` |

## CLI 接口

### 完整流程

```bash
python scripts/execute.py "OpenAI 最新模型发布"
python scripts/execute.py "OpenAI 最新模型发布" --publish
```

### 单阶段执行

```bash
# 搜索
python scripts/execute.py search "OpenAI 最新模型发布"

# 写作（支持 .json/.md/.txt）
python scripts/execute.py write --source research.json
python scripts/execute.py write --source external.md

# 配图
python scripts/execute.py illustrate --source article.json

# 发布
python scripts/execute.py publish --source article_with_images.json --channel wechat
python scripts/execute.py publish --source article_with_images.json --channel xiaohongshu
```

### 向后兼容

```bash
# 旧参数继续有效
python scripts/execute.py "OpenAI" --publish
python scripts/execute.py --source-path /path/to/material.md "基于素材写作"
```

## 子命令参数

| 子命令 | 必需参数 | 可选参数 | 输入格式 |
|--------|---------|---------|---------|
| `search` | `topic` | `--output-dir`, `--num-results` | 无 |
| `write` | `--source` | `--output-dir` | `.json` / `.md` / `.txt` |
| `illustrate` | `--source` | `--output-dir` | `.json` |
| `publish` | `--source` | `--channel`, `--output-dir` | `.json` |

## 数据结构

### research.json

```json
{
  "topic": "OpenAI 最新模型发布",
  "created_at": "2026-03-30T12:00:00Z",
  "search_results": [
    {"title": "...", "url": "https://...", "snippet": "..."}
  ],
  "sources": [
    {"url": "https://...", "content": "抓取内容..."}
  ]
}
```

### article.json

```json
{
  "title": "...",
  "author": "...",
  "digest": "...",
  "cover_prompt": "...",
  "sections": [
    {"content": "<p>...</p>", "image_prompt": "..."}
  ]
}
```

### article_with_images.json

```json
{
  "title": "...",
  "author": "...",
  "digest": "...",
  "cover_prompt": "...",
  "sections": [...],
  "images": {
    "cover": "~/.ikaros/articles/.../images/cover.png",
    "section_0": "~/.ikaros/articles/.../images/section_0.png"
  }
}
```

## 阶段验证规则

| 阶段 | 验证规则 | 失败处理 |
|------|---------|---------|
| search | 至少 1 个有效来源 | recoverable 错误 |
| write | title 非空、≥1 section、总字数 ≥ 200 | recoverable 错误 |
| illustrate | 封面图生成成功 | recoverable 错误 |
| publish | API 返回成功 | fatal（凭证问题）或 recoverable（网络问题） |

## 错误类型

| 错误类型 | failure_mode | 示例 |
|---------|--------------|------|
| 搜索无结果 | recoverable | "未找到相关资料，请调整主题" |
| LLM 返回无效 JSON | recoverable | "文章生成失败，可重试" |
| 封面图生成失败 | recoverable | "配图生成失败，可重试" |
| 微信 IP 白名单问题 | fatal | "IP 不在白名单，需手动配置" |
| opencli 未安装 | fatal | "请先安装 opencli" |
| 发布 API 失败 | recoverable | "发布失败，可重试" |

## 实现要点

### StageResult 数据类

```python
@dataclass
class StageResult:
    ok: bool
    data: dict | None
    output_path: str | None
    error: str | None
    failure_mode: str | None  # "recoverable" | "fatal"
```

### 编排器逻辑

```python
async def run_full_flow(topic, publish, channels, output_dir):
    # Stage 1: Search
    result = await search_stage(topic, output_dir)
    if not result.ok:
        return result

    # Stage 2: Write
    result = await write_stage(result.output_path, output_dir)
    if not result.ok:
        return result

    # Stage 3: Illustrate
    result = await illustrate_stage(result.output_path, output_dir)
    if not result.ok:
        return result

    # Stage 4: Publish (optional)
    if publish:
        result = await publish_stage(result.output_path, channels, output_dir)

    return result
```

## 迁移计划

1. 创建 `stages/` 和 `utils/` 目录结构
2. 提取 `WeChatPublisher` 到 `utils/wechat.py`
3. 提取小红书发布逻辑到 `utils/xiaohongshu.py`
4. 创建 `utils/article.py`，定义 `ArticleData` 和工具函数
5. 实现各阶段模块 `stages/*.py`
6. 重构 `execute.py` 为编排器 + CLI 路由
7. 更新 `SKILL.md` 元数据和文档
8. 迁移测试用例

## 文件行数估计

| 文件 | 行数 |
|------|------|
| `execute.py` | ~200 |
| `stages/search.py` | ~150 |
| `stages/write.py` | ~200 |
| `stages/illustrate.py` | ~150 |
| `stages/publish.py` | ~100 |
| `utils/article.py` | ~150 |
| `utils/wechat.py` | ~120 |
| `utils/xiaohongshu.py` | ~200 |
| **总计** | ~1270 |