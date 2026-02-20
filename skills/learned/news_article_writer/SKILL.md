---
api_version: v3
name: news_article_writer
description: 搜索最新新闻，深度抓取内容，撰写高质量微信公众号文章，支持自动配图和发布到公众号草稿箱。
triggers:
  - 写公众号文章
  - 生成新闻文章
  - 撰写文章
  - 深度报道
params:
  topic: string
  publish: boolean
input_schema:
  type: object
  properties: {}
permissions:
  filesystem: workspace
  shell: false
  network: limited
entrypoint: scripts/execute.py
---

# 微信公众号文章生成器 (All-in-One)

这是一个**全自动、原子化**的技能，用于生成高质量的微信公众号文章。

**⚠️ 重要提示 (IMPORTANT):**
1.  **本技能是全能型工具**：它内部已经集成了搜索、抓取、撰写、绘图和发布的所有逻辑。
2.  **严禁拆解任务**：当用户请求写文章时，请直接调用此技能。**绝不要**尝试先调用搜索工具、绘图工具或其他技能。
3.  **禁止外部委托**：Agent 必须等待此技能返回最终结果，不要在中间插入任何操作。

## 核心能力

1.  **News Aggregation**: 内部自动调用搜索。
2.  **Generate & Illustrate**: 内部自动撰写并绘图。
3.  **Publish to WeChat**: 内部自动发布。

## 执行指令 (SOP)

1.  收到用户请求后，提取 `topic`。如果用户提到“发布”、“上传”等词汇，设置 `publish=True`。
2.  自动调用 `execute.py` 脚本：
    - 搜索并抓取网页内容。
    - 生成文章内容（包含吸引人的标题、摘要、正文）。
    - 绘制封面图。
    - (可选) 获取 `wechat_official_account` 凭证，上传封面和文章到草稿箱。
3.  脚本返回最终的文章、图片和发布结果。

## 依赖配置

如需使用发布功能，请先使用 `account_manager` 配置公众号凭证：

`账号 add service=wechat_official_account app_id=YOUR_APP_ID app_secret=YOUR_APP_SECRET`

## 参数说明

| 参数名 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `topic` | string | 是 | 文章主题 |
| `publish` | boolean | 否 | 是否发布到公众号 (默认 false) |

## 意图映射示例

**1. 撰写文章**
- 用户输入: "写一篇关于ChatGPT最新功能的公众号文章"
- 提取参数:
  ```json
  { "topic": "ChatGPT最新功能", "publish": false }
  ```

**2. 撰写并发布**
- 用户输入: "写一篇关于DeepSeek的文章并发布到公众号"
- 提取参数:
  ```json
  { "topic": "DeepSeek", "publish": true }
  ```
