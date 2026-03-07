---
api_version: v3
name: software_delivery
description: "**软件交付流水线**。统一处理 issue 阅读、规划、实现、验证、发布与技能模板开发。"
triggers:
- 软件开发
- 改代码
- 修复 bug
- GitHub issue
- PR
- software_delivery
- skill_create
- skill_modify
allowed_roles:
- manager
input_schema:
  type: object
  properties: {}
permissions:
  filesystem: workspace
  shell: true
  network: limited
entrypoint: scripts/execute.py
---

# Software Delivery (开发交付 SOP)

这是 Manager 专用技能。入口脚本是 `scripts/execute.py`，通过 `bash` 调用。不要直接手工选择底层编码后端。

## 使用方式

```bash
cd skills/builtin/software_delivery
python scripts/execute.py <action> [options]
```

## 何时使用

- 用户要求开发、改代码、修 bug、处理 GitHub issue、提交 PR。
- 用户要求创建或修改技能。
- 需要查看已有交付任务的状态、日志、恢复执行。

## 常用 action

- `read_issue`
  读取 GitHub issue 内容。
- `plan`
  生成开发计划。
- `run`
  启动完整交付流水线。
- `implement`
  进入编码实现阶段。
- `validate`
  执行验证步骤。
- `publish`
  处理 commit / push / PR 发布。
- `status`
  查询任务状态。
- `logs`
  读取任务日志尾部。
- `resume`
  恢复已有任务。
- `skill_create`
  用模板创建新技能。
- `skill_modify`
  用模板修改现有技能。
- `skill_template`
  只生成技能模板材料。

## 推荐 SOP

1. 如果用户给了 GitHub issue，先用 `read_issue`。
2. 只是想先拆计划，用 `plan`。
3. 真正要执行开发时，用 `run`；技能开发优先用 `skill_create` / `skill_modify`。
4. 发起后如果是异步任务，后续用 `status` / `logs` / `resume` 跟踪，不要重复发起相同 `run`。

## 参数重点

- `requirement`
  任务需求摘要；没有时可回退为用户原话。
- `instruction`
  更明确的执行指令。
- `issue`
  GitHub issue URL 或 `owner/repo#number`。
- `repo_path` / `repo_url`
  仓库定位信息。
- `task_id`
  查询状态、日志、恢复任务时使用。
- `skill_name`
  技能模板动作时的目标技能名。

## 示例

```bash
cd skills/builtin/software_delivery
python scripts/execute.py read_issue --issue acme/x-bot#42
python scripts/execute.py skill_modify --skill-name stock_watch --requirement "恢复脚本入口并重写 SOP，禁止模型自行写脚本"
python scripts/execute.py status --task-id dev-20260306-001
```

## 禁止事项

- Manager 侧编码问题不要先用 `bash` 试探，应优先进入 `software_delivery`。
- 不要绕过此工具直接调用底层 `coding_backend`。
