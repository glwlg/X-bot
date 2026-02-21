---
api_version: v3
name: web_extractor
description: Extracts clean, ad-free Markdown content from any web page URL. Use this skill FIRST whenever the user wants to read an article, summarize a webpage, or extract text from a URL. Do NOT use web_browser unless you specifically need interactive capabilities (like clicking, logging in, or taking visual screenshots).
input_schema:
  type: object
  properties:
    url:
      type: string
      description: 目标网页的URL
  required:
    - url
permissions:
  network: limited
entrypoint: scripts/execute.py
---

# Web Extractor Skill

This skill uses `r.jina.ai` under the hood to quickly extract the main content of any given URL into clean, readable Markdown. It handles JavaScript-rendered pages and strips out ads, sidebars, and navigation menus automatically.

## Usage

Provide the `url` parameter. The skill will yield the Markdown content of the page.

### Example

Input:
`{"url": "https://example.com/article"}`

Output:
A string containing the Markdown representation of the page's main content.

## When to use

- "总结一下这个链接：https://..." -> Use `web_extractor`
- "帮我提取这篇公众号文章" -> Use `web_extractor`
- "打开这个网站，点击登录按钮" -> Use `web_browser` (Interaction required)
- "截取这个网页的图" -> Use `web_browser` (Visual snapshot required)
