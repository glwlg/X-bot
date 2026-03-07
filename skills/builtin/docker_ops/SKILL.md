---
api_version: v3
name: docker_ops
description: "**Docker 底层操作指南**。直接管理容器、网络，不可用于文件操作；文件操作请使用内置四原语 `read/write/edit/bash`。这是你需要阅读的SOP，用于教导你如何使用 bash 直接管理 Docker。"
triggers:
- docker
- 容器
- container
- remove
- delete
- 删除
- 移除
permissions:
  filesystem: workspace
  shell: true
  network: limited
---

# Docker Ops (容器运维指南)

你是一个 Docker 运维与执行工具。

## 核心能力与操作指南

请在接收到用户指定的意图后，**直接拼接对应的 Shell 命令并使用 `bash` 或者直接进行写文件/读文件：**

1.  **管理容器**: 
    - 停止容器: `docker stop <name>`
    - 删除容器: `docker rm <name>`
    - 查看容器: `docker ps -a`

2.  **执行命令**: 在宿主机通过 `bash` 运行相关验证命令（如验证网络状态、测试连接）

3.  **Compose 操作**: 在指定目录执行操作。如果要在指定工作目录下运行，请记得 `cd <path> && docker compose up -d`
    - 启动: `docker compose up -d --build`
    - 停止: `docker compose down`
    - 修改文件: 请使用原生的写文件/补丁工具（例如 `write_to_file` / `apply_patch` / `edit_file` / 或使用 `bash` 配合 `cat` / `sed` / `echo` 等）去修改 `docker-compose.yml`

## 常见操作意图与原子工具执行示例

**1. 列出容器**
- 用户输入: "查看运行中的容器"
- **LLM 执行策略**: 直接调用 `bash` 工具，执行 `docker ps -a`

**2. 在目录执行 compose up**
- 用户输入: "在 uptime-kuma 项目目录启动服务"
- **LLM 执行策略**: 拿到绝对路径后，调用 `bash` 工具，执行 `cd /path/to/uptime-kuma && docker compose up -d --build`

**3. 停止并删除容器**
- 用户输入: "删除 caddy 容器"
- **LLM 执行策略**: 调用 `bash` 工具，执行 `docker stop caddy && docker rm caddy` 或 `docker rm -f caddy`

**4. 写入并在特定目录执行命令**
- 用户输入: "在 myapp 项目目录配置变量并执行 docker compose logs"
- **LLM 执行策略**: 
    - 使用原生操作（如 `apply_patch` 或 `bash` 搭配 `echo`）配置变量文件
    - 然后调用 `bash` 工具，执行 `cd /path/to/myapp && docker compose logs --tail 50`

## 安全限制与约定

禁止或限制的操作：
- 避免执行大范围无区别重启或删除命令 (如 `docker rm -f $(docker ps -aq)`)，除非用户明确授权。
- 如果不确定 Docker Daemon 状态，可先用 `docker info` 或 `systemctl status docker` (有权限的话) 验证。
