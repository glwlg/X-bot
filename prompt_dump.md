# Prompt Context Analysis

**Generated at**: 2026-01-28 12:52:33.836054
**Total Characters**: 4830
**MCP Memory Enabled**: True

## Breakdown
- System Prompt: 756 (15.7%)
- Tools Definition: 1790 (37.1%)
- History (20 msgs): 2284 (47.3%)

## 1. System Prompt
```text
你是一个友好的助手。请用中文回复。

【记忆管理指南】
请遵循以下步骤进行交互：

1. **身份识别**：
   - 始终将当前交互用户视为实体 'User'。

2. **记忆检索（Memory Retrieval）**：
   - **仅在必要时**（例如用户询问个人信息、偏好或历史时）才使用 `open_nodes(names=['User'])`。
   - **禁止**在普通问答（如“你好”、“翻译这个”、“这个视频讲了什么”）或明确的操作指令（如“下载视频”、“列出订阅”、“查看服务”）中调用记忆工具。
   - **优先级规则**：如果其他工具（如 `list_subscriptions`, `download_video`, `list_containers`, `deploy_github_repo`）能解决问题，**绝对不要**调用 `open_nodes`。
   - 如果用户没有询问与自己相关的信息，请直接回答，不要调用任何工具。

3. **记忆更新（Memory Update）**：
   - 在对话中时刻关注以下类别的新信息：
     a) **基本身份**：年龄、性别、居住地（Location）、职业等。
     b) **行为习惯**、**偏好**、**目标**、**关系**等。

   - 当捕获到新信息时：
     a) 使用 `create_entities` 为重要的人、地点、组织创建实体。
     b) 使用 `create_relations` 将它们连接到 'User'（例如：Relation('User', 'lives in', '无锡')）。
     c) 使用 `add_observations` 存储具体的观察事实。

```

## 2. Tools Definitions
```json
[
  {
    "name": "call_skill",
    "description": "Call an installed skill. Available skills:\n- download_video\n- deployment_manager\n- docker_ops\n- reminder\n- monitor_keyword\n- rss_subscribe\n- stock_watch\n- searxng_search\n- translate_mode\n- data-storytelling\n- weather\n- data-quality-frameworks\n- coingecko\n- context7\n- xlsx\n(Descriptions omitted for brevity. Infer usage from name or search.)",
    "parameters": "{'skill_name': Schema(\n  description='The exact name of the skill to call',\n  type=<Type.STRING: 'STRING'>\n), 'instruction': Schema(\n  description=\"The user's natural language instruction for the skill\",\n  type=<Type.STRING: 'STRING'>\n)}"
  },
  {
    "name": "search_skill",
    "description": "Search for a skill/tool in the marketplace when you lack a capability. Returns a list of candidates. DO NOT install automatically.",
    "parameters": "{'query': Schema(\n  description='Search query',\n  type=<Type.STRING: 'STRING'>\n)}"
  },
  {
    "name": "install_skill",
    "description": "Install a specific skill. ONLY call this if the user has explicitly confirmed.",
    "parameters": "{'repo_name': Schema(\n  description=\"The repository name (e.g. 'owner/repo')\",\n  type=<Type.STRING: 'STRING'>\n), 'skill_name': Schema(\n  description='The skill name',\n  type=<Type.STRING: 'STRING'>\n)}"
  },
  {
    "name": "modify_skill",
    "description": "Modify an existing skill's code based on natural language instructions. Use this to fix bugs, add features, or change configuration (e.g. API URLs).",
    "parameters": "{'skill_name': Schema(\n  description='The name of the skill to modify',\n  type=<Type.STRING: 'STRING'>\n), 'instruction': Schema(\n  description='Instructions for modification',\n  type=<Type.STRING: 'STRING'>\n)}"
  }
]
```

## 3. Simulated History
```json
[
  {
    "role": "user",
    "parts": [
      "This is user message #1. It might be a bit longer than hello."
    ]
  },
  {
    "role": "model",
    "parts": [
      "This is AI response #1. I am helpful and polite."
    ]
  },
  {
    "role": "user",
    "parts": [
      "This is user message #2. It might be a bit longer than hello."
    ]
  },
  {
    "role": "model",
    "parts": [
      "This is AI response #2. I am helpful and polite."
    ]
  },
  {
    "role": "user",
    "parts": [
      "This is user message #3. It might be a bit longer than hello."
    ]
  },
  {
    "role": "model",
    "parts": [
      "This is AI response #3. I am helpful and polite."
    ]
  },
  {
    "role": "user",
    "parts": [
      "This is user message #4. It might be a bit longer than hello."
    ]
  },
  {
    "role": "model",
    "parts": [
      "This is AI response #4. I am helpful and polite."
    ]
  },
  {
    "role": "user",
    "parts": [
      "This is user message #5. It might be a bit longer than hello."
    ]
  },
  {
    "role": "model",
    "parts": [
      "This is AI response #5. I am helpful and polite."
    ]
  },
  {
    "role": "user",
    "parts": [
      "This is user message #6. It might be a bit longer than hello."
    ]
  },
  {
    "role": "model",
    "parts": [
      "This is AI response #6. I am helpful and polite."
    ]
  },
  {
    "role": "user",
    "parts": [
      "This is user message #7. It might be a bit longer than hello."
    ]
  },
  {
    "role": "model",
    "parts": [
      "This is AI response #7. I am helpful and polite."
    ]
  },
  {
    "role": "user",
    "parts": [
      "This is user message #8. It might be a bit longer than hello."
    ]
  },
  {
    "role": "model",
    "parts": [
      "This is AI response #8. I am helpful and polite."
    ]
  },
  {
    "role": "user",
    "parts": [
      "This is user message #9. It might be a bit longer than hello."
    ]
  },
  {
    "role": "model",
    "parts": [
      "This is AI response #9. I am helpful and polite."
    ]
  },
  {
    "role": "user",
    "parts": [
      "This is user message #10. It might be a bit longer than hello."
    ]
  },
  {
    "role": "model",
    "parts": [
      "This is AI response #10. I am helpful and polite."
    ]
  }
]
```
