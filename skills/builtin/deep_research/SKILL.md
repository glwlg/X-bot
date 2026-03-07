---
api_version: v3
name: deep_research
description: 深度研究与长文报告生成。适合需要多源搜索、网页抓取和综合分析的问题。
triggers:
- deep research
- 深度研究
- 深入研究
- 深度分析
- deep dive
- 调研
- 研究
- 研究报告
input_schema:
  type: object
  properties:
    topic:
      type: string
      description: 研究主题或问题
    depth:
      type: integer
      description: 读取和分析的网页数量，建议 3-10
      minimum: 1
      maximum: 10
      default: 5
    language:
      type: string
      description: 搜索语言，例如 zh-CN、en-US
      default: zh-CN
  required:
  - topic
permissions:
  filesystem: workspace
  shell: true
  network: limited
entrypoint: scripts/execute.py
---

# Deep Research

通过 `bash` 运行 `python scripts/execute.py ...`。脚本会搜索、抓取并生成 Markdown 报告；默认把文件写到当前目录，也可以用 `--output-dir` 指定目录。

## Command

- `python scripts/execute.py "<topic>" --depth 5 --language zh-CN`

## Output

- 终端会输出研究过程文本。
- 最终报告会保存为 `deep_research_report.md`，并打印 `saved_file=...` 路径。

## Rules

- 仅在问题需要多来源综合研究时使用。
- 研究深度默认 5，除非问题很简单或用户明确要求更多/更少来源。
