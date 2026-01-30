---
name: deployment_manager
description: 自主部署代理。用于所有复杂的部署请求,处理规划、部署、修复错误和验证结果(端口 > 20000, HTTP 200)
triggers:
- manage_deployment
---
# Deployment Manager



## 使用方法

**触发词**: `manage_deployment`

## 参数

- **goal**: The high-level deployment goal (e.g. 'Deploy SearXNG')
- **repo_url**: Optional GitHub URL if known

## 实现

此技能使用 `scripts/execute.py` 实现核心逻辑。
