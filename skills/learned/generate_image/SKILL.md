---
api_version: v3
name: generate_image
description: "**文生图**。根据提示词生成图片并返回图片文件。"
triggers:
- 画图
- 生成图片
- 绘图
- 文生图
- image
- draw
- paint
- imagine
runtime_target: worker
change_level: learned
allow_manager_modify: true
allow_auto_publish: true
rollout_target: worker
preflight_commands:
- python scripts/execute.py --help
policy_groups:
- media
platform_handlers: false
input_schema:
  type: object
  properties:
    prompt:
      type: string
      description: 图片提示词
    aspect_ratio:
      type: string
      description: 输出比例
      enum:
      - "1:1"
      - "16:9"
      - "9:16"
      - "4:3"
      - "3:4"
permissions:
  filesystem: workspace
  shell: true
  network: limited
entrypoint: scripts/execute.py
---

# Generate Image

这是一个文生图 skill。入口固定是 `scripts/execute.py`，由当前模型配置里的 image 模型负责生成图片。

## 使用方式

```bash
cd skills/learned/generate_image
python scripts/execute.py "<prompt>" [--aspect-ratio 1:1|16:9|9:16|4:3|3:4]
```

## 参数

- `<prompt>`
  必填，想生成的画面描述。
- `--aspect-ratio`
  可选，默认 `1:1`。

## 推荐 SOP

1. 用户只说“画一张……”时，直接把用户原话整理成 prompt。
2. 用户没指定比例时，默认 `1:1`。
3. 宽图用 `16:9`，竖图用 `9:16`，海报或封面常用 `3:4`。
4. 不要自己拼 HTTP 请求，不要绕过 `scripts/execute.py`。

## 示例

```bash
cd skills/learned/generate_image
python scripts/execute.py "画一只赛博朋克风格的猫"
python scripts/execute.py "日落下的海边木屋，电影感，暖色调" --aspect-ratio 16:9
```

## 输出

- 成功时会输出文本摘要，并把图片写到当前目录或 `--output-dir` 指定目录。
- CLI 会额外输出 `saved_file=<绝对路径>`，总结结果时以这个路径为准。
