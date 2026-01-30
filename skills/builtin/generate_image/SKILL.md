---
name: generate_image
description: 使用 AI 生成图片 (Imagen 3)
triggers:
- 画图
- 生成图片
- 绘图
- image
- paint
- draw
- imagine
---
# Generate Image

文生图 Skill - 使用 Gemini Imagen 生成图片

## 使用方法

**触发词**: `画图`, `生成图片`, `绘图`, `image`, `paint`

## 参数

- **prompt** (`str`) (必需): 画面描述 (提示词)
- **aspect_ratio** (`str`) (必需): 长宽比，可选: 1:1, 16:9, 9:16, 4:3, 3:4

## 实现

此技能使用 `scripts/execute.py` 实现核心逻辑。
