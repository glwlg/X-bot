---
api_version: v3
name: deployment_manager
description: 智能部署代理。用于部署项目、查看部署状态、查询访问地址、验证可访问性和清理部署目录。
triggers:
- deploy
- 部署
- manage_deployment
- 安装服务
- install
input_schema:
  type: object
  properties:
    action:
      type: string
      enum:
      - auto_deploy
      - status
      - delete_project
      - get_access_info
      - verify_access
  required:
  - action
permissions:
  filesystem: workspace
  shell: true
  network: limited
entrypoint: scripts/execute.py
---

# Deployment Manager

这是部署编排 skill。用 `bash` 调脚本，不要在回复里口头描述“将调用某个内部动作”。工作目录由 `X_DEPLOYMENT_STAGING_PATH` 控制；若环境未配置，脚本会回退到仓库内默认目录。

## Commands

- 自动部署：`python scripts/execute.py auto-deploy "<request>" [--service <name>] [--repo-url <url>] [--host-port 20080]`
- 查看部署状态：`python scripts/execute.py status`
- 获取访问地址：`python scripts/execute.py access-info <project_name>`
- 验证服务可达：`python scripts/execute.py verify-access [--name <project_name>] [--url <url>] [--timeout 10]`
- 删除部署目录：`python scripts/execute.py delete-project <project_name>`

## Rules

- 部署完成后必须再执行一次 `verify-access`，不要只凭容器启动日志判断成功。
- 宿主机端口优先使用 20000 以上端口。
- 删除项目目录前要确认用户确实要求清理，而不是仅停止服务。
