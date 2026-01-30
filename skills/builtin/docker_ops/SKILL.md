---
name: docker_ops
description: NATIVE DOCKER MANAGER. Handles DEPLOYMENT, STOPPING, and REMOVING containers. Use this skill to deploy apps from GitHub, stop services, and DELETE/REMOVE containers and volumes. DO NOT SEARCH FOR EXTERNAL SKILLS FOR DOCKER ACTIONS.
triggers:
- docker
- 容器
- container
- remove
- delete
- 删除
- 移除
---
# Docker Ops



## 使用方法

**触发词**: `deploy_github_repo`, `list_containers`, `stop_container`, `list_networks`, `run_docker_command`

## 参数

- **action**: Action: 'list_services', 'list_networks', 'stop', 'deploy', 'execute_command', 'edit_file', 'remove', 'delete'
- **url**: GitHub URL (for 'deploy')
- **name**: Container or Project name (for 'stop')
- **is_compose**: Boolean (for 'stop'), treats name as compose project if True
- **remove**: Boolean (for 'stop'), if True, removes container (rm) or stops project (down)
- **clean_volumes**: Boolean (for 'stop'), if True, removes named volumes (only with remove=True)
- **command**: Raw docker command to run (for 'execute_command', e.g. 'docker logs caddy')
- **path**: File path to edit (for 'edit_file')
- **content**: New file content (for 'edit_file')

## 实现

此技能使用 `scripts/execute.py` 实现核心逻辑。
