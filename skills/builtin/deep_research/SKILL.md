---
name: deep_research
description: 执行深度网络研究。不仅搜索链接，还会自动访问搜索结果页面，读取正文内容，并进行深度整合与摘要，生成综合研究报告。
triggers:
- deep research
- 深度研究
- 深入研究
- 深度分析
- deep dive
---
# Deep Research (深度研究)

强大的深度信息挖掘能力，用于处理复杂的查询，需要综合多个信息源才能回答的问题。

## 工作原理

1. **广度搜索**: 首先使用搜索引擎发现相关页面。
2. **深度爬取**: 自动访问并读取 Top N 结果的完整正文。
3. **智能综合**: 如果页面内容过长，自动提取关键信息。
4. **报告生成**: 将多方来源的信息整合为一份详细的 HTML 报告。

## 参数

- **topic** (`str`) (必需): 研究主题或问题。
- **depth** (`int`) (可选): 爬取深度（阅读的页面数量），默认为 3，最大 5。建议值：3-5。
- **language** (`str`) (可选): 搜索语言，默认 zh-CN。

## 适用场景

- "深入研究 DeepSeek V3 的技术架构"
- "帮我做一份关于 2024 AI 行业发展的深度报告"
- "Research the latest advancements in solid-state batteries"
