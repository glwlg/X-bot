# Service Tool Symlinks

`ikaros` 在 systemd 这类非交互环境里启动时，不会自动继承 `fnm`、`nvm`、`asdf` 等 shell 初始化出来的 PATH。

当前 [run_ikaros.sh](/home/luwei/workspace/ikaros/scripts/run_ikaros.sh) 只保证 `~/.local/bin` 会进入服务进程的 PATH，所以依赖 Node.js 的外部 CLI 需要在这里放稳定软链。

## Required for `opencli`

最少需要这两个软链：

```bash
ln -sfn "$HOME/.local/share/fnm/node-versions/<version>/installation/bin/node" "$HOME/.local/bin/node"
ln -sfn "$HOME/.local/share/fnm/node-versions/<version>/installation/bin/opencli" "$HOME/.local/bin/opencli"
```

原因：

- `opencli` 本身需要可执行文件名 `opencli`
- `opencli` 的 shebang 是 `#!/usr/bin/env node`，所以运行它时还必须能在 PATH 里找到 `node`

## Optional helpers

如果后续还要在服务环境里排查 Node 工具问题，可以额外补：

```bash
ln -sfn "$HOME/.local/share/fnm/node-versions/<version>/installation/bin/npm" "$HOME/.local/bin/npm"
ln -sfn "$HOME/.local/share/fnm/node-versions/<version>/installation/bin/npx" "$HOME/.local/bin/npx"
```

## Verify

建立软链后，建议至少确认：

```bash
which node
which opencli
opencli --version
```

如果服务已经在跑，补完软链后重启对应 systemd unit，让新 PATH 生效。
