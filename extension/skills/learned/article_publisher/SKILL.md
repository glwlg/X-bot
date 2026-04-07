---
api_version: v3
name: article_publisher
description: 生成长文或图文发布稿，也支持直接基于本地 md/txt 素材写作；可自动搜索资料、生成配图，并支持多渠道发布，当前已支持微信公众号和小红书发布通道。支持按阶段（search/write/illustrate/publish）单独执行或全流程编排。
triggers:
- 文章发布
- 写文章
- 公众号文章
- 写公众号
- 写长文
- 深度报道
- 热点解读
runtime_target: ikaros
change_level: learned
allow_ikaros_modify: true
allow_auto_publish: true
rollout_target: ikaros
preflight_commands:
- python scripts/execute.py --help
policy_groups:
- content
- research
platform_handlers: false
input_schema:
  type: object
  properties:
    topic:
      type: string
      description: 文章主题、写作要求或新闻话题。提供本地素材时可选。
    source_path:
      type: string
      description: 本地 md/txt 素材路径。提供后跳过搜索，直接基于素材写作。
    source_paths:
      type: array
      items:
        type: string
      description: 多个本地 md/txt 素材路径。提供后跳过搜索，直接基于素材写作。
    publish:
      type: boolean
      description: 是否发布到所选渠道
    publish_channel:
      type: string
      description: 发布或导出渠道，支持 wechat、xiaohongshu
    publish_channels:
      type: array
      items:
        type: string
      description: 多个发布或导出渠道，支持 wechat、xiaohongshu
    wechat_account:
      type: string
      description: 可选，指定用于发布的公众号凭据别名或 ID；未传时优先用默认项，否则回退第一条公众号凭据。
    stage:
      type: string
      enum: [search, write, illustrate, publish]
      description: 可选，指定单独执行某个阶段。不传则执行全流程。
    source:
      type: string
      description: 单阶段模式下的输入文件路径（research.json / article.json / article_with_images.json）。
    word_count:
      type: integer
      description: 正文目标字数，默认为 1000。
  anyOf:
  - required:
    - topic
  - required:
    - source_path
  - required:
    - source_paths
  - required:
    - stage
permissions:
  filesystem: workspace
  shell: true
  network: limited
entrypoint: scripts/execute.py
---

# Article Publisher

这是长文写作与多渠道发布 skill。入口固定是 `scripts/execute.py`；搜索阶段内部复用 `web_search`，正文抓取继续走 `fetch_webpage_content`，不要在外部手工拆成多个 skill。

## Architecture

内部拆分为 4 个可组合阶段：

```
[search] ──research.json──► [write] ──article.json──► [illustrate] ──article_with_images.json──► [publish]
```

目录结构：
```
scripts/
├── execute.py           # 编排器 + CLI 入口
├── stages/
│   ├── __init__.py      # StageResult 数据类
│   ├── search.py        # 搜索阶段
│   ├── write.py         # 写作阶段
│   ├── illustrate.py    # 配图阶段
│   └── publish.py       # 发布阶段
└── utils/
    ├── __init__.py      # ArticleData、JSON 解析、通用工具
    ├── wechat.py        # WeChatPublisher 类
    └── xiaohongshu.py   # 小红书发布逻辑
```

## Command

### 全流程

- `python scripts/execute.py "OpenAI 最新模型发布"`
- `python scripts/execute.py "DeepSeek 新进展" --publish`
- `python scripts/execute.py "AI 应用观察" --publish --wechat-account 主号`
- `python scripts/execute.py "AI 工作流" --publish-channel xiaohongshu`
- `python scripts/execute.py "AI 工作流" --publish --publish-channel wechat --publish-channel xiaohongshu`
- `python scripts/execute.py --source-path /abs/path/to/video_text.md "基于素材写一篇教程"`

### 单阶段执行

- `python scripts/execute.py "OpenAI" --stage search`
- `python scripts/execute.py --stage write --source research.json`
- `python scripts/execute.py --stage illustrate --source article.json`
- `python scripts/execute.py --stage publish --source article_with_images.json --publish-channel wechat`

### 机器调用

- `python scripts/execute.py "OpenAI 最新模型发布" --raw-json`

## Rules

- 适用于文章写作、热点综述、公众号长文和图文发布稿，不适用于普通网页摘要。
- 内部搜索统一走 `ctx.run_skill("web_search", {...})`；查询词应从用户指令里提炼主题，必要时附带新闻分类、时间范围和排除对象，不要把整段长指令原样塞进搜索词。
- 面向公众号读者的文章默认按“可直接发布的正文”生成，不应夹带“以下是文章 / 免责声明 / 责编 / END / 图片来源”等非正文信息。
- 只有当用户明确要求“新闻 / 快讯 / 资讯 / 当天新闻”这类时效内容时，技能才应切到新闻综述模式；普通文章、教程、观点稿或基于本地材料改写时，不要强行按新闻稿写。
- 当提供 `source_path/source_paths` 时，不进行搜索，也不抓网页；直接基于本地 md/txt 素材写作，但仍然生成配图。
- `publish=true` 且未显式指定渠道时，默认按 `wechat` 兼容旧行为。
- 发布到公众号前，先用 `credential_manager` 配置 `wechat_official_account` 的 `app_id` 与 `app_secret`；同一用户下可保存多条公众号凭据。
- 指定 `wechat_account` 时，按别名或凭据 ID 选择对应公众号；未指定时优先使用默认项，没有默认项则回退第一条公众号凭据。
- 发布到小红书前，先用 `credential_manager` 配置 `xiaohongshu_publisher` 的 `endpoint=`，可选 `token=`、`api_key=`、`author=`。当前小红书走可配置发布通道，不假设存在官方公开内容发布 API。
- `wechat_official_account` 和 `xiaohongshu_publisher` 都支持统一配置 `author=`；文章作者优先使用发布渠道账户里的这个值，图片水印自动派生为 `@author`。
- 如果通过 `bash` 让 bot 执行 CLI，优先追加 `--raw-json`；CLI 会用 `tool_result=...` 输出最终结构化结果，避免把进度文本误当成 shell 错误。
- 支持通过 `--stage` 参数单独执行某个阶段，中间产物通过 JSON 文件传递，支持断点续跑。

## Performance & Batching Rules

- **耗时警告**: `article_publisher` 的 `write` 和 `illustrate` 阶段非常耗时（单次全流程需 2-5 分钟），且包含大量内容的结构化输出。
- **批量并发生成**: 如果用户要求生成**多篇**文章（例如切割了 3 份素材要求写 3 篇），**严禁**在同一个回合中串行多次调用 `bash`，这极易导致大模型 API 超时卡死（Timeout/Hang）或受到超长上下文截断限制！
- **必须使用子智能体**: 遇到批量生成需求时，你**必须**使用 `spawn_subagent` 工具，将每一篇文章的生成任务分配给独立的子智能体（subagent）去并发执行。例如：为 part1, part2, part3 分别派生 3 个子智能体，赋予它们相关的工具权限让他们独立去执行对应文件的 `execute.py` 并等待其完成。

## Credential Example

- `python skills/builtin/credential_manager/scripts/execute.py add wechat_official_account --data 'app_id=xxx app_secret=yyy author=炜煜'`
- `python skills/builtin/credential_manager/scripts/execute.py add xiaohongshu_publisher --data 'endpoint=https://publisher.example.com/xhs token=xxx author=炜煜'`

## Output

- 成功时返回文章正文、生成的图片文件，以及可选的多渠道发布结果。
- 当选择 `xiaohongshu` 渠道时，会额外生成 `xiaohongshu_note.txt` 和 `xiaohongshu_note.json` 作为发布草稿附件。
- CLI 会把附件写到默认输出目录或 `--output-dir` 指定目录，并输出 `saved_file=<绝对路径>`。
- 中间产物存放在 `{DATA_DIR}/user/skills/article_publisher/articles/{topic_slug}/` 下。
