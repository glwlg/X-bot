#!/usr/bin/env python3
"""
为所有 SKILL.md 添加 triggers 字段到 frontmatter
"""
import os
import yaml
import re
from pathlib import Path


# 技能触发词映射(从原 Python 文件的 SKILL_META 提取)
SKILL_TRIGGERS = {
    # Builtin
    "download_video": ["下载", "download", "save", "保存视频", "视频下载", "get video"],
    "web_browser": ["访问", "browse", "打开网页", "查看网页", "网页", "阅读", "read", "summarize"],
    "skill_manager": ["search_skill", "install_skill", "delete_skill", "list_skills", "check_updates", "update_skills", "modify_skill"],
    "deployment_manager": ["manage_deployment"],
    "notebooklm": ["notebooklm", "notebook", "podcast", "播客"],
    "docker_ops": ["docker", "容器", "container"],
    "reminder": ["提醒", "remind", "timer", "定时", "闹钟", "alarm"],
    "monitor_keyword": ["monitor", "监控", "watch", "关注"],
    "rss_subscribe": ["rss", "订阅", "subscribe", "feed"],
    "stock_watch": ["stock", "股票", "自选股", "add_stock", "remove_stock"],
    "generate_image": ["画图", "生成图片", "绘图", "image", "paint", "draw", "imagine"],
    "searxng_search": ["search", "搜索", "查找", "find", "google"],
    "translate_mode": ["translate", "翻译", "translation"],
}


def add_triggers_to_skill(skill_dir: Path, skill_name: str):
    """为单个技能添加 triggers"""
    skill_md_path = skill_dir / "SKILL.md"
    
    if not skill_md_path.exists():
        return False
    
    # 读取文件
    with open(skill_md_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 解析 frontmatter
    if not content.startswith("---"):
        print(f"  ⚠️  {skill_name}: 没有 frontmatter,跳过")
        return False
    
    parts = content.split("---", 2)
    if len(parts) < 3:
        print(f"  ⚠️  {skill_name}: frontmatter 格式错误,跳过")
        return False
    
    frontmatter = yaml.safe_load(parts[1])
    
    # 检查是否已有 triggers
    if "triggers" in frontmatter and frontmatter["triggers"]:
        print(f"  ⏭️  {skill_name}: 已有 triggers,跳过")
        return False
    
    # 获取触发词
    triggers = SKILL_TRIGGERS.get(skill_name)
    if not triggers:
        print(f"  ⚠️  {skill_name}: 未找到触发词定义,跳过")
        return False
    
    # 添加 triggers
    frontmatter["triggers"] = triggers
    
    # 重新组装
    new_content = "---\n" + yaml.dump(frontmatter, allow_unicode=True, sort_keys=False) + "---" + parts[2]
    
    # 写回
    with open(skill_md_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    print(f"  ✅ {skill_name}: 已添加 {len(triggers)} 个触发词")
    return True


def main():
    """主函数"""
    project_root = Path(__file__).parent.parent
    builtin_dir = project_root / "skills" / "builtin"
    
    print("=" * 60)
    print("🔧 为 builtin 技能添加 triggers 字段")
    print("=" * 60)
    
    success_count = 0
    for skill_dir in builtin_dir.iterdir():
        if skill_dir.is_dir() and not skill_dir.name.startswith('_'):
            if add_triggers_to_skill(skill_dir, skill_dir.name):
                success_count += 1
    
    print("\n" + "=" * 60)
    print(f"✅ 完成: {success_count} 个技能已添加 triggers")
    print("=" * 60)


if __name__ == "__main__":
    main()
