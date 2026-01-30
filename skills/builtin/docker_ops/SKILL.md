---
name: docker_ops
description: NATIVE DOCKER MANAGER. CAPABLE OF DEPLOYING GITHUB REPOS DIRECTLY. Use
  this skill to deploy applications (e.g. SearXNG, Typecho) from GitHub URLs, manage
  containers, and edit docker-compose.yml. DO NOT SEARCH FOR EXTERNAL SKILLS FOR DOCKER
  DEPLOYMENT.
triggers:
- docker
- 容器
- container
---
# Docker Ops



## 使用方法

**触发词**: `deploy_github_repo`, `list_containers`, `stop_container`, `list_networks`, `run_docker_command`

## 参数

- **action**: Action: 'list_services', 'list_networks', 'stop', 'deploy', 'execute_command', 'edit_file'
- **url**: GitHub URL (for 'deploy')
- **name**: Container name (for 'stop')
- **is_compose**: Boolean (for 'stop')
- **command**: Raw docker command to run (for 'execute_command', e.g. 'docker logs caddy')
- **path**: File path to edit (for 'edit_file')
- **content**: New file content (for 'edit_file')

## 实现

此技能使用 `scripts/execute.py` 实现核心逻辑。
