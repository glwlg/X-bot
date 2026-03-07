#!/bin/bash

# ================= 配置区 =================
# 获取当前脚本所在的项目根目录
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." &> /dev/null && pwd)"

# 想要备份的文件或目录列表（绝对路径，空格分隔）
SOURCE_DIRS=(
  "$PROJECT_DIR/data"
  "$PROJECT_DIR/config"
  "$PROJECT_DIR/.env"
  # 这里可以继续添加系统中的其他目录
)

# 临时存放压缩包的目录和文件名
WORK_DIR="/tmp/xbot_backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="backup_${TIMESTAMP}.tar.gz"

# Rclone 云端目标目录（配置名需要与你在 rclone config 中设置的名称一致，假设配置名为 onedrive）
# 将上传到 OneDrive 根目录下的 Backups/XBot 文件夹中
RCLONE_DEST="onedrive:backup/XBot"

# 最多保留多少天的备份？
KEEP_DAYS=14
# ==========================================

# 确定 rclone 执行路径，优先使用系统命令，否则查找用户私有目录
if command -v rclone >/dev/null 2>&1; then
    RCLONE_CMD="rclone"
elif [ -x "$HOME/.local/bin/rclone" ]; then
    RCLONE_CMD="$HOME/.local/bin/rclone"
else
    echo "❌ 找不到 rclone 命令，请先安装。"
    exit 1
fi

mkdir -p "$WORK_DIR"
cd "$WORK_DIR" || exit

echo "开始打包备份文件..."
# 将目标目录全部打入 tar.gz，丢弃无用输出
tar -czf "$BACKUP_FILE" "${SOURCE_DIRS[@]}" > /dev/null 2>&1

if [ $? -eq 0 ]; then
    echo "打包成功: $BACKUP_FILE, 开始上传至 OneDrive..."
    # 上传文件
    "$RCLONE_CMD" copy "$BACKUP_FILE" "$RCLONE_DEST"
    
    if [ $? -eq 0 ]; then
        echo "✅ 上传成功！"
        
        # 删除本地的临时文件
        rm -f "$BACKUP_FILE"
        echo "清理本地临时压缩包完成。"
        
        # 清理远端超过 N 天的旧备份
        echo "清理云端超过 $KEEP_DAYS 天的旧备份..."
        "$RCLONE_CMD" delete "$RCLONE_DEST" --min-age "${KEEP_DAYS}d"

        echo "🎉 备份流程全部完成时间: $(date)"
    else
        echo "❌ 上传到 OneDrive 失败。"
    fi
else
    echo "❌ 打包失败，请检查目录权限。"
fi
