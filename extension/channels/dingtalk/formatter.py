"""
DingTalk Markdown Formatter

钉钉 Markdown 语法支持有限，需要做以下转换：
1. 表格 -> 代码块
2. HTML 标签 -> 纯文本
3. 复杂格式简化
"""

import re


def markdown_to_dingtalk_compat(text: str) -> str:
    """
    Convert generic Markdown to DingTalk-friendly Markdown.

    DingTalk Markdown 支持:
    - 标题 (#, ##, ###)
    - 加粗 (**text**)
    - 链接 [text](url)
    - 图片 ![alt](url)
    - 有序/无序列表
    - 引用 (>)

    DingTalk Markdown 不支持:
    - 表格 (会渲染为原始文本)
    - 代码高亮
    - 斜体 (*text* 可能不支持)
    """
    if not text:
        return ""

    # 1. 移除 HTML 标签 (钉钉不支持)
    text = re.sub(r"<[^>]+>", "", text)

    # 2. 将 Markdown 表格转换为代码块
    lines = text.split("\n")
    in_table = False
    table_buffer = []
    output_lines = []

    def flush_table():
        if not table_buffer:
            return
        # 将表格包装在代码块中以保持可读性
        output_lines.append("```")
        output_lines.extend(table_buffer)
        output_lines.append("```")
        table_buffer.clear()

    for i, line in enumerate(lines):
        stripped = line.strip()
        is_table_row = stripped.startswith("|") and stripped.endswith("|")

        if is_table_row:
            if not in_table:
                # 检查下一行是否是表格分隔符 |---|
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    if next_line.startswith("|") and "-" in next_line:
                        in_table = True

            if in_table:
                table_buffer.append(line)
            else:
                output_lines.append(line)
        else:
            if in_table:
                in_table = False
                flush_table()
            output_lines.append(line)

    # 处理文件末尾的表格
    if in_table:
        flush_table()

    return "\n".join(output_lines)


def escape_dingtalk_markdown(text: str) -> str:
    """
    Escape special characters for DingTalk Markdown.
    """
    if not text:
        return ""
    # 钉钉 Markdown 相对宽松，但某些字符可能需要转义
    # 目前保持简单，不做额外转义
    return text
