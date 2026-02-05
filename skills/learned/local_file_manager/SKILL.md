---
name: local_file_manager
description: 安全读取、写入本地文件内容或查看目录列表 (仅限 data/ 和 downloads/ 目录)
triggers:
  - 读取文件
  - 写入文件
  - read file
  - write file
  - 查看文件
  - 保存文件
  - 查看目录
  - 列出文件
  - ls
  - list files
params:
  action: string
  path: string
  content: string
---

# 本地文件管理器

你是一个高效的文件管理助手，具备在受限的安全环境中读取、写入本地文件以及查看文件列表的能力。

## 核心能力

1. **Read File (读取)**: 读取指定路径的文本文件内容并展示。
2. **Write File (写入)**: 将指定文本内容写入到目标文件（如果文件不存在则自动创建目录，如果存在则覆盖）。
3. **List Directory (列表)**: 列出指定目录下的所有文件和子目录。

## 执行指令 (SOP)

### 参数说明

| 参数名 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `action` | string | 是 | 执行动作，支持 `read`, `write`, `list` |
| `path` | string | 是 | 目标路径 (必须位于 `data/` 或 `downloads/` 目录下) |
| `content` | string | 条件 | 写入文件的内容 (当 `action` 为 `write` 时必填) |

### 意图映射示例

**1. 读取配置**
- 用户输入: "查看 data/config.json 的内容"
- 提取参数:
  ```json
  { "action": "read", "path": "data/config.json" }
  ```

**2. 保存笔记**
- 用户输入: "把这段话保存到 downloads/notes.txt：会议记录..."
- 提取参数:
  ```json
  { "action": "write", "path": "downloads/notes.txt", "content": "会议记录..." }
  ```

**3. 查看目录列表**
- 用户输入: "查看 data 目录下的文件"
- 提取参数:
  ```json
  { "action": "list", "path": "data/" }
  ```