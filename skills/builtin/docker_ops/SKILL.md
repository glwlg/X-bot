---
name: docker_ops
description: "**Docker 底层操作接口**。直接管理容器、网络和文件。通常由 `deployment_manager` 调用，也可用于简单的容器管理。"
triggers:
- docker
- 容器
- container
- remove
- delete
- 删除
- 移除
---

# Docker Ops (容器运维)

你是一个 Docker 运维与执行工具。

## 核心能力

1.  **管理容器**: 停止、删除、查看容器。
2.  **执行命令**: 在宿主机执行 Docker 相关命令。
3.  **部署服务**: 拉取代码并启动服务。

## 执行指令 (SOP)

### 参数说明

| 参数名 | 类型 | 必填 | 说明 |
| :--- | :--- | :--- | :--- |
| `action` | string | 是 | 操作类型 (见下表) |
| `url` | string | 条件 | GitHub 仓库地址 (deploy) |
| `name` | string | 条件 | 容器或项目名称 (stop/remove) |
| `command` | string | 条件 | Shell 命令 (execute_command) |
| `is_compose` | boolean | 否 | 是否为 compose 项目 (stop/remove) |
| `remove` | boolean | 否 | 是否删除容器 (stop) |
| `clean_volumes` | boolean | 否 | 是否清理卷 (stop + remove) |

### 可用 Action

| Action | 说明 |
| :--- | :--- |
| `list_services` | 列出运行中的服务 |
| `stop` | 停止/删除容器或项目 |
| `deploy` | 部署 GitHub 仓库 |
| `execute_command` | 执行 Shell 命令 |
| `edit_file` | 编辑文件 (docker-compose.yml) |
| `list_networks` | 列出网络 |

### 意图映射示例

**1. 列出容器**
- 用户输入: "查看运行中的容器"
- 提取参数:
  ```json
  { "action": "list_services" }
  ```

**2. 停止并删除容器**
- 用户输入: "删除 caddy 容器"
- 提取参数:
  ```json
  { "action": "stop", "name": "caddy", "remove": true }
  ```

**3. 执行命令**
- 用户输入: "在宿主机执行 docker ps"
- 提取参数:
  ```json
  { "action": "execute_command", "command": "docker ps" }
  ```
