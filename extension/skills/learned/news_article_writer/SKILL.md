---
api_version: v3
name: news_article_writer
description: 为新闻或热点主题生成长文，也支持直接基于本地 md/txt 素材写作；可自动搜索资料、生成配图，并支持多渠道发布，当前已支持微信公众号和小红书发布通道。
triggers:
- 新闻文章
- 公众号文章
- 写公众号
- 写长文
- 深度报道
- 热点解读
runtime_target: manager
change_level: learned
allow_manager_modify: true
allow_auto_publish: true
rollout_target: manager
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
  anyOf:
  - required:
    - topic
  - required:
    - source_path
  - required:
    - source_paths
permissions:
  filesystem: workspace
  shell: true
  network: limited
entrypoint: scripts/execute.py
---

# News Article Writer

这是新闻长文写作 skill。入口固定是 `scripts/execute.py`；搜索阶段内部复用 `web_search`，正文抓取继续走 `fetch_webpage_content`，不要在外部手工拆成多个 skill。

## Command

- `python scripts/execute.py "OpenAI 最新模型发布"`
- `python scripts/execute.py "DeepSeek 新进展" --publish`
- `python scripts/execute.py "AI 工作流" --publish-channel xiaohongshu`
- `python scripts/execute.py "AI 工作流" --publish --publish-channel wechat --publish-channel xiaohongshu`
- `python scripts/execute.py --source-path /abs/path/to/video_text.md "基于素材写一篇教程"`
- 机器调用时使用：`python scripts/execute.py "OpenAI 最新模型发布" --raw-json`

## Rules

- 适用于新闻解读、热点综述、公众号长文，不适用于普通网页摘要。
- 内部搜索调用保持 `ctx.run_skill("web_search", {"query": topic, "num_results": 8})`；这是当前 `web_search` skill 支持的正确参数形式。
- 当提供 `source_path/source_paths` 时，不进行搜索，也不抓网页；直接基于本地 md/txt 素材写作，但仍然生成配图。
- `publish=true` 且未显式指定渠道时，默认按 `wechat` 兼容旧行为。
- 发布到公众号前，先用 `credential_manager` 配置 `wechat_official_account` 的 `app_id` 与 `app_secret`。
- 发布到小红书前，先用 `credential_manager` 配置 `xiaohongshu_publisher` 的 `endpoint=`，可选 `token=`、`api_key=`、`author=`。当前小红书走可配置发布通道，不假设存在官方公开内容发布 API。
- `wechat_official_account` 和 `xiaohongshu_publisher` 都支持统一配置 `author=`；文章作者优先使用发布渠道账户里的这个值，图片水印自动派生为 `@author`。
- 如果通过 `bash` 让 bot 执行 CLI，优先追加 `--raw-json`；CLI 会用 `tool_result=...` 输出最终结构化结果，避免把进度文本误当成 shell 错误。

## Credential Example

- `python skills/builtin/credential_manager/scripts/execute.py add wechat_official_account --data 'app_id=xxx app_secret=yyy author=炜煜'`
- `python skills/builtin/credential_manager/scripts/execute.py add xiaohongshu_publisher --data 'endpoint=https://publisher.example.com/xhs token=xxx author=炜煜'`

## Output

- 成功时返回文章正文、生成的图片文件，以及可选的多渠道发布结果。
- 当选择 `xiaohongshu` 渠道时，会额外生成 `xiaohongshu_note.txt` 和 `xiaohongshu_note.json` 作为发布草稿附件。
- CLI 会把附件写到默认输出目录或 `--output-dir` 指定目录，并输出 `saved_file=<绝对路径>`。
