---
api_version: v3
name: xlsx
description: 分析 Excel 文件基础结构，返回工作表数量和名称。
license: Proprietary. LICENSE.txt has complete terms
input_schema:
  type: object
  properties: {}
permissions:
  filesystem: workspace
  shell: true
  network: limited
entrypoint: scripts/execute.py
triggers: []
---

# XLSX

当前 CLI 只提供文件分析能力。通过 `bash` 执行脚本读取本地 Excel 文件，不要假设它已经支持创建或修改工作簿。

## Command

- `python scripts/execute.py analyze <file_path>`

## Rules

- 路径必须是本地可访问的 Excel 文件。
- 如果用户要求复杂编辑、生成报表或公式修复，需要结合 `read/write/edit/bash` 另外实现，而不是假装本脚本已支持。
