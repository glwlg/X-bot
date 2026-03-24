# Rclone 备份配置指南

为了将 Ikaros 的核心数据目录 (`data/`, `config/`, `.env` 等) 安全稳定地定期备份到云盘（如 OneDrive），我们采用了优秀的开源工具 **Rclone**。

如果你需要在新的服务器或者给其他协作者配置备份功能，请按照以下步骤进行。

## 1. 安装 Rclone 工具

在你的 Linux 宿主机上，你可以使用官方一键安装脚本（需要 sudo 权限）：

```bash
sudo -v ; curl https://rclone.org/install.sh | sudo bash
```

如果是在免 Root 权限下，你可以使用二进制手动安装（如前文通过下载官方发行包，放置于 `~/.local/bin/`）。

## 2. 获取云盘授权 Token（本地操作）

因为大部分服务器没有图形化浏览器，我们需要在 **拥有浏览器的电脑（比如你日常使用的 Windows/macOS）** 上获取登录凭证。

1. 在你的本地电脑下载一份对应的 [Rclone](https://rclone.org/downloads/) 工具。
2. 打开本地终端（cmd、PowerShell 或终端.app），执行如下命令：

```bash
rclone authorize "onedrive"
```
*(注意：这里将打开浏览器，请登录你的微软账号并同意授权)*
3. 授权成功后，回到命令行窗口，等待其输出一段以 `{"access_token": ...}` 开头的 **完整 JSON 字符串**。
4. 将这段完整的 JSON 字符串原封不动地复制下来。

## 3. 在服务器配置远程储存 (Server 操作)

在你的服务器终端，执行配置命令以添加一个新的云盘 Remote：

```bash
rclone config
```

按照以下交互提示输入：
1. `n/s/q>` **选择 `n`** （新建一个 remote）。
2. `name>` **输入 `onedrive`**。（注意：这里的名字对应 `backup.sh` 脚本中的变量 `RCLONE_DEST`，不可随便改）。
3. `Storage>` **找到 Microsoft OneDrive 并输入对应数字**（目前通常是 `31`，也可能是其他，仔细看一下列表里的 OneDrive 选项）。
4. `client_id>` 和 `client_secret>` **直接按回车跳过**。
5. `region>` **选择 `1` (global) 或直接回车**。
6. `Edit advanced config?` **选择 `n`**。
7. `Use web browser to automatically authenticate?` **选择 `n`**（因为服务器没有浏览器）。
8. `result>` **直接将刚才从本地电脑获取到的整串 JSON Token 粘贴回击**。
9. `Type of connection>` **选择 `1` (OneDrive Personal or Business)**。
10. `Chose drive to use>` 如果显示出你的云盘名称，直接输入它的编号并确认。
11. 最后连按回车并选择 `q` 保存并退出。

你可以使用下面的命令测试连接是否顺畅。如果能打印出你在 OneDrive 里存放的文件名，即代表联机成功：
```bash
rclone ls onedrive:
```

## 4. 设置自动调度 (Crontab)

要让这一切真正达到自动化、无人值守的备份效果，需要挂载定时的 Cron 任务。

1. 给代码库里的备份脚本增加可执行权限：
   ```bash
   chmod +x ./scripts/backup.sh
   ```
2. 编辑当前用户的定时排程：
   ```bash
   crontab -e
   ```
3. 在文件底部添加这一行记录（表示 **每天凌晨 3:00** 运行备份，并且记录执行日志到 `ikaros_backup_log.txt` 中）：
   ```cron
   0 3 * * * /你的项目绝对路径/scripts/backup.sh >> /tmp/ikaros_backup_log.txt 2>&1
   ```

## 注意事项与常见排错

- 脚本设置了 `KEEP_DAYS=14`，它会自动帮你在每次运行完毕时检查 `onedrive:backup/Ikaros` 目录下的超期打包文件并删除，因此你不必担心云盘容量爆棚。
- 如果某天突然备份失效了（报错通常会在 `>> /tmp/ikaros_backup_log.txt` 日志），大部分是因为长期未动造成的**云端 Token 失效**，此时只需要 `rclone config`，删除原本的 `onedrive` remote，并重复一遍上文所述的『第 2 步到第 3 步』重新完成授权即可续时拉起服务。
