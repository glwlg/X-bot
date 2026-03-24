---
api_version: v3
name: news_article_writer
description: 为新闻或热点主题生成公众号长文，自动搜索资料、生成配图，并可发布到微信草稿箱。
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
      description: 文章主题或新闻话题
    publish:
      type: boolean
      description: 是否发布到微信公众号草稿箱
  required:
  - topic
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
- 机器调用时使用：`python scripts/execute.py "OpenAI 最新模型发布" --raw-json`

## Rules

- 适用于新闻解读、热点综述、公众号长文，不适用于普通网页摘要。
- 内部搜索调用保持 `ctx.run_skill("web_search", {"query": topic, "num_results": 8})`；这是当前 `web_search` skill 支持的正确参数形式。
- 发布到公众号前，先用 `account_manager` 配置 `wechat_official_account` 的 `app_id` 与 `app_secret`。
- `wechat_official_account` 里统一配置 `author=`；文章作者直接使用这个值，图片水印自动派生为 `@author`。
- 如果通过 `bash` 让 bot 执行 CLI，优先追加 `--raw-json`；CLI 会用 `tool_result=...` 输出最终结构化结果，避免把进度文本误当成 shell 错误。

## Account Example

- `python skills/builtin/account_manager/scripts/execute.py add wechat_official_account --data 'app_id=xxx app_secret=yyy author=炜煜'`

## Output

- 成功时返回文章正文、生成的图片文件，以及可选的公众号草稿箱发布结果。
- CLI 会把附件写到默认输出目录或 `--output-dir` 指定目录，并输出 `saved_file=<绝对路径>`。
