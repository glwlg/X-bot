---
name: download_video
description: 从 URL 下载视频或音频，支持 YouTube, Bilibili, Twitter/X, TikTok 等平台。自动识别 URL。
triggers:
- 下载
- download
- save
- 保存视频
- 视频下载
- get video
---
# Download Video

视频下载 Skill - 下载视频/音频

## 使用方法

**触发词**: `下载`, `download`, `save`, `保存视频`, `视频下载`

## 参数

- **url** (`str`) (必需): 视频链接
- **format** (`str`): 下载格式，默认 video

## 实现

此技能使用 `scripts/execute.py` 实现核心逻辑。
