---
api_version: v3
name: opencli
description: OpenCLI 命令参考。仅当用户明确提到 `opencli`，并且就是想直接查看或运行已安装的 `opencli` 命令时才使用本 skill。对于通用搜索、下载、写作、发布或多步骤工作流，如果已有更贴合领域的 skill，就不要使用本 skill。
triggers:
- opencli
- opencli doctor
permissions:
  filesystem: workspace
  shell: true
  network: limited
---

# OpenCLI 使用说明

直接使用当前环境中已安装的 `opencli`。本 skill 只用于调用现成的 `opencli` 命令。

不要因为目标平台“恰好被 OpenCLI 支持”就选这个 skill。
如果用户没有明确要求使用 `opencli`，而且已有其他领域 skill 能覆盖任务，应优先使用领域 skill。
特别是当系统里已经存在更高层的 skill 时，不要让本 skill 接管诸如“下载视频 -> 转写 -> 写文章 -> 发布”这种端到端多步骤工作流。

本 skill **刻意不覆盖** 以下内容：

- adapter 的创建或修改
- `explore` / `probe`、`synthesize`、`generate`、`record`、`cascade`
- `install`、`register` 或 plugin 开发

## 版本

当前环境已确认：

`opencli --version` -> `1.4.1`

## 默认使用流程

1. 先查看当前有哪些能力：

   `opencli list --format md`

2. 再查看某个平台的帮助：

   `opencli <platform> --help`

3. 再查看具体命令的帮助：

   `opencli <platform> <command> --help`

4. 需要结构化结果时，优先使用结构化输出：

   支持时优先使用 `-f json`、`-f md`、`-f yaml` 或 `-f csv`

5. 如果浏览器驱动类命令失败，先做诊断：

   `opencli doctor --sessions`

## 前置条件

- 很多命令依赖浏览器会话。执行前要确保 Chrome 正在运行，并且已经登录目标网站。
- 浏览器驱动类命令依赖 Chrome 里已可用的 OpenCLI Browser Bridge 扩展。
- 某些命令执行时会临时打开标签页，结束后再关闭。
- 对于 `post`、`publish`、`reply`、`follow`、`like`、`delete` 等会修改账号状态的命令，只有在用户明确要求时才执行。

## 发现命令

```bash
opencli --help
opencli list --format md
opencli doctor --no-live
opencli doctor --sessions
opencli <platform> --help
opencli <platform> <command> --help
```

`opencli list --format md` 是当前环境里最权威的命令清单。输出里会包含 `command`、`site`、`description`、`strategy`、`browser`、`args` 等字段，适合用来判断命令是否依赖浏览器，以及它期望的参数形式。

## 常见用法

### 读取或搜索内容

```bash
opencli xiaohongshu search "美食"
opencli bilibili hot --limit 10
opencli twitter search "AI"
opencli zhihu question 34816524
opencli xueqiu stock SH600519
opencli reddit search "rust" --limit 10
```

### 下载或导出内容

```bash
opencli weixin download --url "https://mp.weixin.qq.com/..."
opencli zhihu download --url "https://zhuanlan.zhihu.com/p/..."
opencli xiaohongshu download <note-id> --output ./downloads
opencli twitter article <tweet-id>
opencli web read --url "https://example.com" --output article.md
```

### 发布内容或修改账号状态

```bash
opencli xiaohongshu publish "正文内容" --title "标题" --images a.png,b.png --topics 话题1,话题2
opencli twitter post "Hello world"
opencli twitter reply https://x.com/... "Nice!"
opencli twitter like https://x.com/...
```

### 查看创作者或账号数据

```bash
opencli xiaohongshu creator-profile
opencli xiaohongshu creator-stats
opencli xiaohongshu creator-notes --limit 10
opencli bilibili me
opencli twitter profile elonmusk
```

## 当前安装里较常用的平台

以下示例来自本机 `opencli --help` / `opencli list --format md`：

- 社交和内容平台：`xiaohongshu`、`twitter`、`bilibili`、`zhihu`、`reddit`、`weibo`、`youtube`、`douyin`
- 文章和导出：`weixin`、`web`、`zhihu download`、`twitter article`
- 财经和新闻：`xueqiu`、`barchart`、`yahoo-finance`、`reuters`、`bloomberg`、`bbc`
- 桌面和 AI 应用：`codex`、`cursor`、`chatgpt`、`chatwise`、`discord-app`、`doubao-app`
- 外部 CLI：`gh`、`docker`

不要把上面这份列表当成完整清单。只要平台集合本身会影响决策，就重新执行 `opencli list --format md` 再确认。

## 实用规则

- 优先按 `--help` 展示的形式使用位置参数；很多命令会把 `query`、`id`、`url`、`text` 设计成位置参数。
- 如果结果要被代码解析，或者要喂给下一步流程，优先用 `-f json`。
- 如果要生成面向用户的报告或保存文档，优先用 `-f md`。
- 如果命令含义不够明确，先看 `--help`，不要直接猜。
- 如果用户提到某个平台，但你不确定当前安装里是否真的支持，先查 `opencli list --format md`，不要臆测。
