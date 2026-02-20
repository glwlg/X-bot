#!/usr/bin/env python3
"""
翻译现有 learned 技能的英文描述为中文
"""
import os
import yaml
import asyncio
from pathlib import Path


async def translate_description(description: str) -> str:
    """使用 Gemini 翻译描述"""
    from google import genai
    
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise ValueError("GEMINI_API_KEY not set")
    
    client = genai.Client(api_key=api_key)
    
    prompt = f"将以下技能描述翻译为简洁的中文,保持专业性,不要添加任何解释:\n\n{description}"
    
    response = await client.aio.models.generate_content(
        model="gemini-2.0-flash-exp",
        contents=prompt
    )
    
    return response.text.strip()


async def process_skill(skill_dir: Path):
    """处理单个技能"""
    skill_md_path = skill_dir / "SKILL.md"
    
    if not skill_md_path.exists():
        return
    
    # 读取 SKILL.md
    with open(skill_md_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 解析 frontmatter
    if not content.startswith("---"):
        return
    
    parts = content.split("---", 2)
    if len(parts) < 3:
        return
    
    frontmatter = yaml.safe_load(parts[1])
    description = frontmatter.get("description", "")
    
    # 检测是否为英文
    if not description or any('\u4e00' <= char <= '\u9fff' for char in description):
        print(f"  ⏭️  {skill_dir.name}: 已是中文,跳过")
        return
    
    print(f"  🔄 {skill_dir.name}: 翻译中...")
    print(f"     原文: {description[:60]}...")
    
    # 翻译
    chinese_desc = await translate_description(description)
    print(f"     译文: {chinese_desc[:60]}...")
    
    # 更新 frontmatter
    frontmatter["description"] = chinese_desc
    
    # 重新组装
    new_content = "---\n" + yaml.dump(frontmatter, allow_unicode=True, sort_keys=False) + "---" + parts[2]
    
    # 写回
    with open(skill_md_path, 'w', encoding='utf-8') as f:
        f.write(new_content)
    
    print(f"  ✅ {skill_dir.name}: 翻译完成")


async def main():
    """主函数"""
    project_root = Path(__file__).parent.parent
    learned_dir = project_root / "skills" / "learned"
    
    print("=" * 60)
    print("🌐 翻译 learned 技能描述为中文")
    print("=" * 60)
    
    # 遍历所有技能
    for skill_dir in learned_dir.iterdir():
        if skill_dir.is_dir() and not skill_dir.name.startswith('_'):
            await process_skill(skill_dir)
    
    print("\n" + "=" * 60)
    print("✅ 翻译完成")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
