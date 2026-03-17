---
api_version: v3
name: download_video
description: "**下载视频或音频**。使用内置下载脚本抓取在线视频并返回落盘路径。"
triggers:
- 下载
- download
- save
- 保存视频
- 视频下载
- get video
policy_groups:
- media
platform_handlers: true
input_schema:
  type: object
  properties: {}
permissions:
  filesystem: workspace
  shell: true
  network: limited
entrypoint: scripts/execute.py
---

# Download Video (视频下载服务)

此技能已经自带完整下载能力，入口是 `scripts/execute.py`，底层由 `scripts/services/download_service.py` 负责下载、路径管理与大文件判断。

## 固定存放路径

- 下载文件统一保存到 **项目根目录** 下的 `downloads/`。
- 脚本执行成功后会输出：
  - `download_dir=<绝对目录>`
  - `saved_path=<绝对文件路径>`
  - `is_too_large=true|false`
- 如果 `is_too_large=true`，文件依然保留在同一个 `downloads/` 目录里，供后续处理。

## 使用方式

通过 `bash` 在技能目录执行：

```bash
cd skills/builtin/download_video
python scripts/execute.py <url> [--format video|audio]
```

## 参数

- `<url>`
  必填，目标视频地址。
- `--format video`
  默认值，下载最佳可用视频。
- `--format audio`
  只提取音频，输出 mp3。

## 推荐 SOP

1. 用户未明确格式时，默认用 `--format video`。
2. 用户明确要 mp3、音频、只听声音时，用 `--format audio`。
3. 下载后读取脚本输出里的 `saved_path`，再告诉用户真实落盘位置。
4. **不要** 自己拼 `yt-dlp` 命令，不要自定义输出目录，不要绕过脚本。

## 示例

```bash
cd skills/builtin/download_video
python scripts/execute.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
python scripts/execute.py "https://www.bilibili.com/video/BV1xx411c7mD" --format audio
```

## 注意事项

- 路径与 cookies 文件由代码内部管理，不要自行指定 `-o` 或额外输出目录。
- 该脚本会在 stderr 输出进度，在 stdout 输出最终结果字段；总结结果时以 stdout 为准。
