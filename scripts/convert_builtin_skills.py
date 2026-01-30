#!/usr/bin/env python3
"""
自动转换 builtin 技能从 Python 格式到标准 SKILL.md 格式
"""
import os
import ast
import shutil
import re
from pathlib import Path
from typing import Dict, Any, Optional


def parse_skill_meta(filepath: str) -> Optional[Dict[str, Any]]:
    """解析 Python 文件中的 SKILL_META"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
        
        tree = ast.parse(source)
        
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "SKILL_META":
                        meta = ast.literal_eval(node.value)
                        return meta
        
        return None
    except Exception as e:
        print(f"❌ 解析 {filepath} 失败: {e}")
        return None


def extract_docstring(filepath: str) -> str:
    """提取文件顶部的 docstring"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
        
        tree = ast.parse(source)
        docstring = ast.get_docstring(tree)
        return docstring or ""
    except:
        return ""


def extract_execute_function(filepath: str) -> str:
    """提取 execute 函数及其依赖的 imports"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        # 找到所有 import 语句
        imports = []
        in_skill_meta = False
        skill_meta_start = -1
        skill_meta_end = -1
        
        for i, line in enumerate(lines):
            # 跳过 SKILL_META 定义
            if 'SKILL_META' in line and '=' in line:
                in_skill_meta = True
                skill_meta_start = i
            
            if in_skill_meta:
                if '}' in line:
                    skill_meta_end = i
                    in_skill_meta = False
                continue
            
            # 收集 import 语句
            if line.strip().startswith(('import ', 'from ')):
                imports.append(line)
        
        # 找到 execute 函数
        execute_start = -1
        for i, line in enumerate(lines):
            if 'async def execute' in line:
                execute_start = i
                break
        
        if execute_start == -1:
            return ""
        
        # 提取 execute 函数到文件末尾
        execute_lines = lines[execute_start:]
        
        # 组合 imports + execute
        result = ''.join(imports) + '\n' + ''.join(execute_lines)
        return result
        
    except Exception as e:
        print(f"❌ 提取 execute 函数失败: {e}")
        return ""


def generate_skill_md(meta: Dict[str, Any], docstring: str) -> str:
    """生成 SKILL.md 内容"""
    name = meta.get('name', 'unknown')
    description = meta.get('description', '')
    triggers = meta.get('triggers', [])
    params = meta.get('params', {})
    
    # 构建 frontmatter
    frontmatter = f"""---
name: {name}
description: {description}
---
"""
    
    # 构建主体
    body = f"""# {name.replace('_', ' ').title()}

{docstring}

## 使用方法

**触发词**: {', '.join(f'`{t}`' for t in triggers[:5])}

"""
    
    # 添加参数说明
    if params:
        body += "## 参数\n\n"
        if isinstance(params, dict):
            for param_name, param_info in params.items():
                # 处理两种格式: 字典或字符串
                if isinstance(param_info, dict):
                    param_type = param_info.get('type', 'str')
                    param_desc = param_info.get('description', '')
                    required = '' if param_info.get('optional', False) else ' (必需)'
                    body += f"- **{param_name}** (`{param_type}`){required}: {param_desc}\n"
                else:
                    # 简单字符串描述
                    body += f"- **{param_name}**: {param_info}\n"
        body += "\n"
    
    body += """## 实现

此技能使用 `scripts/execute.py` 实现核心逻辑。
"""
    
    return frontmatter + body


def convert_skill(py_file: Path, builtin_dir: Path, backup_dir: Path) -> bool:
    """转换单个技能"""
    skill_name = py_file.stem
    print(f"\n🔄 转换 {skill_name}...")
    
    # 解析 SKILL_META
    meta = parse_skill_meta(str(py_file))
    if not meta:
        print(f"  ⚠️  未找到 SKILL_META,跳过")
        return False
    
    # 提取 docstring
    docstring = extract_docstring(str(py_file))
    
    # 提取 execute 函数
    execute_code = extract_execute_function(str(py_file))
    if not execute_code:
        print(f"  ⚠️  未找到 execute 函数,跳过")
        return False
    
    # 创建目录结构
    skill_dir = builtin_dir / skill_name
    scripts_dir = skill_dir / "scripts"
    
    skill_dir.mkdir(exist_ok=True)
    scripts_dir.mkdir(exist_ok=True)
    
    # 生成 SKILL.md
    skill_md = generate_skill_md(meta, docstring)
    skill_md_path = skill_dir / "SKILL.md"
    with open(skill_md_path, 'w', encoding='utf-8') as f:
        f.write(skill_md)
    
    # 写入 execute.py
    execute_path = scripts_dir / "execute.py"
    with open(execute_path, 'w', encoding='utf-8') as f:
        f.write(execute_code)
    
    # 备份原文件
    backup_dir.mkdir(exist_ok=True)
    shutil.copy2(py_file, backup_dir / py_file.name)
    
    # 删除原文件
    py_file.unlink()
    
    print(f"  ✅ 转换完成: {skill_dir}")
    return True


def main():
    """主函数"""
    # 确定路径
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    builtin_dir = project_root / "skills" / "builtin"
    backup_dir = project_root / "skills" / "builtin_backup"
    
    print("=" * 60)
    print("🚀 开始转换 builtin 技能为标准 SKILL.md 格式")
    print("=" * 60)
    
    # 查找所有 .py 文件
    py_files = list(builtin_dir.glob("*.py"))
    py_files = [f for f in py_files if not f.name.startswith('_')]
    
    print(f"\n📋 找到 {len(py_files)} 个技能文件")
    
    # 转换每个文件
    success_count = 0
    for py_file in py_files:
        if convert_skill(py_file, builtin_dir, backup_dir):
            success_count += 1
    
    print("\n" + "=" * 60)
    print(f"✅ 转换完成: {success_count}/{len(py_files)} 个技能")
    print(f"📦 原文件已备份到: {backup_dir}")
    print("=" * 60)
    
    # 列出转换后的目录
    print("\n📂 转换后的目录结构:")
    for item in sorted(builtin_dir.iterdir()):
        if item.is_dir() and not item.name.startswith('_'):
            print(f"  ✓ {item.name}/")
            if (item / "SKILL.md").exists():
                print(f"    ├── SKILL.md")
            if (item / "scripts" / "execute.py").exists():
                print(f"    └── scripts/execute.py")


if __name__ == "__main__":
    main()
