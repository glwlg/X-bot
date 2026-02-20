---
name: file_manager
description: "**本地文件管理**。支持文件的读写、列表查看、删除及发送文件给用户。"
triggers:
- file
- read
- write
- list
- delete
- send file
- cat
- ls
- rm
---

# File Manager (文件管理器)

你是一个本地文件管理器，可以帮助用户管理运行环境中的文件。

## 核心能力

1.  **列出文件 (List)**: 查看目录下的文件和文件夹。
2.  **读取文件 (Read)**: 读取文件内容并显示。
3.  **写入文件 (Write)**: 创建新文件 or 覆盖现有文件。
4.  **删除文件 (Delete)**: 删除指定文件。
5.  **发送文件 (Send)**: 将文件作为 Document 发送给用户 (下载)。

## 执行指令 (SOP)

### 参数说明

| 参数名 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `action` | string | 是 | `list` (列表), `read` (读), `write` (写), `delete` (删), `send` (发) |
| `path` | string | 是 | 文件或目录路径 (支持相对路径或绝对路径) |
| `content` | string | 条件 | 写入文件时的内容 (action=write 时必填) |

### 可用 Action

| Action | 说明 |
| :--- | :--- |
| `list` | 列出目录下的文件和文件夹 |
| `read` | 读取文件内容 |
| `write` | 创建新文件 or 覆盖现有文件 |
| `delete` | 删除指定文件 |
| `send` | 发送文件 |


### 意图映射示例

**1. 列出当前目录文件**
- 用户输入: "查看当前目录下的文件" / "ls"
- 提取参数:
  ```json
  { "action": "list", "path": "." }
  ```

**2. 读取文件内容**
- 用户输入: "读取 data/config.json 的内容" / "cat SKILL.md"
- 提取参数:
  ```json
  { "action": "read", "path": "data/config.json" }
  ```

**3. 写入文件**
- 用户输入: "创建一个名为 hello.txt 的文件，内容是 Hello World"
- 提取参数:
  ```json
  { "action": "write", "path": "hello.txt", "content": "Hello World" }
  ```

**4. 删除文件**
- 用户输入: "删除 temp.log"
- 提取参数:
  ```json
  { "action": "delete", "path": "temp.log" }
  ```

**5. 发送文件**
- 用户输入: "把 logs/app.log 发送给我"
- 提取参数:
  ```json
  { "action": "send", "path": "logs/app.log" }
  ```
