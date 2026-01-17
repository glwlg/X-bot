"""
Z-Library 书籍下载与转换 Skill
支持输入直接下载链接或搜索查询（通过镜像），自动下载 epub/txt 并转换为 markdown
"""
import os
import re
import httpx
import zipfile
import html
from telegram import Update
from telegram.ext import ContextTypes
from utils import smart_reply_text, smart_edit_text

SKILL_META = {
    "name": "zlib_to_md",
    "description": "下载 Z-Library 书籍并转换为 Markdown 格式 (目前主要支持 EPUB 转换)",
    "triggers": ["zlib", "下载书籍", "找书", "book"],
    "params": {
        "query": {
            "type": "str",
            "description": "书籍名称、ISBN 或 Z-Library 的下载链接"
        }
    },
    "version": "1.0.1",
    "author": "X-Bot-Generator"
}

# 伪装 Header
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
}

def clean_html_to_md(html_content: str) -> str:
    """
    简易的 HTML 转 Markdown 函数，不依赖第三方库
    """
    # 解码 HTML 实体
    text = html.unescape(html_content)
    
    # 移除 head, script, style
    text = re.sub(r'<head.*?>.*?</head>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<script.*?>.*?</script>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'<style.*?>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
    
    # 转换标题
    text = re.sub(r'<h1.*?>(.*?)</h1>', r'# \1\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<h2.*?>(.*?)</h2>', r'## \1\n', text, flags=re.IGNORECASE)
    text = re.sub(r'<h3.*?>(.*?)</h3>', r'### \1\n', text, flags=re.IGNORECASE)
    
    # 转换段落
    text = re.sub(r'<p.*?>(.*?)</p>', r'\1\n\n', text, flags=re.IGNORECASE)
    
    # 转换加粗/斜体
    text = re.sub(r'<b.*?>(.*?)</b>', r'**\1**', text, flags=re.IGNORECASE)
    text = re.sub(r'<strong.*?>(.*?)</strong>', r'**\1**', text, flags=re.IGNORECASE)
    text = re.sub(r'<i.*?>(.*?)</i>', r'*\1*', text, flags=re.IGNORECASE)
    text = re.sub(r'<em.*?>(.*?)</em>', r'*\1*', text, flags=re.IGNORECASE)
    
    # 移除剩余标签
    text = re.sub(r'<[^>]+>', '', text)
    
    # 处理多余空行
    text = re.sub(r'\n\s*\n', '\n\n', text)
    return text.strip()

def convert_epub_to_md(epub_path: str, output_path: str):
    """
    解压 EPUB 并合并其中的 HTML 转换为 Markdown
    """
    md_content = []
    
    try:
        with zipfile.ZipFile(epub_path, 'r') as z:
            # 读取 container.xml 寻找 rootfile (OPF)
            # 简化处理：遍历所有 html/xhtml 文件
            file_list = z.namelist()
            html_files = [f for f in file_list if f.endswith(('.html', '.xhtml', '.htm'))]
            
            # 简单排序，尝试按章节顺序（通常文件名有数字索引）
            html_files.sort()
            
            for html_file in html_files:
                with z.open(html_file) as f:
                    content = f.read().decode('utf-8', errors='ignore')
                    md_chunk = clean_html_to_md(content)
                    if md_chunk:
                        md_content.append(md_chunk)
                        
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(f"# {os.path.basename(epub_path)}\n\n")
            f.write("\n\n---\n\n".join(md_content))
            
        return True
    except Exception as e:
        print(f"Conversion error: {e}")
        return False

async def search_annas_archive(query: str, client: httpx.AsyncClient) -> dict:
    """
    使用 Anna's Archive (Z-Lib 聚合镜像) 搜索
    """
    base_url = "https://annas-archive.org/search"
    params = {"q": query, "filetype": "epub"} # 优先搜 epub 方便转换
    
    try:
        resp = await client.get(base_url, params=params, follow_redirects=True)
        if resp.status_code == 200:
            # 这里需要正则提取第一个结果，实际场景建议使用 API 或更复杂的解析
            # 这是一个简化的模拟逻辑，提取第一个可能的详情页链接
            match = re.search(r'href="(/md5/[a-f0-9]{32})"', resp.text)
            if match:
                return {"title": query, "url": f"https://annas-archive.org{match.group(1)}"}
    except Exception:
        pass
    return None

async def execute(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict) -> None:
    user_id = update.effective_user.id
    query = params.get("query", "").strip()
    
    if not query:
        await smart_reply_text(update, "❌ 请提供书籍名称或 Z-Library 下载链接。")
        return

    msg = await smart_reply_text(update, f"🔍 正在搜索/处理: {query} ...")
    
    # 准备工作目录
    work_dir = os.path.join("data", str(user_id), "books")
    os.makedirs(work_dir, exist_ok=True)
    
    target_url = ""
    is_direct_url = query.startswith("http")
    
    async with httpx.AsyncClient(headers=HEADERS, timeout=30.0) as client:
        # 1. 确定下载链接
        if is_direct_url:
            target_url = query
        else:
            # 尝试搜索
            result = await search_annas_archive(query, client)
            if result:
                target_url = result['url']
                await smart_edit_text(msg, f"📚 找到相关书籍，尝试获取内容...\n链接: {target_url}")
                # 注意：Anna's Archive 详情页还需要解析出具体下载链接，这里简化为提示用户
                # 实际自动化下载 Anna/Zlib 需要绕过 Cloudflare，通常建议用户直接给直链
                await smart_edit_text(msg, "⚠️ 自动下载受限，请复制上面的链接到浏览器下载，或发送直接的文件下载链接。")
                return
            else:
                await smart_edit_text(msg, "❌ 未找到相关书籍，请尝试提供精确名称或直接链接。")
                return

        # 2. 下载文件 (假设是直链)
        file_name = "downloaded_book.epub"
        file_path = os.path.join(work_dir, file_name)
        
        try:
            await smart_edit_text(msg, "⬇️ 正在下载文件...")
            async with client.stream("GET", target_url) as response:
                if response.status_code != 200:
                    await smart_edit_text(msg, f"❌ 下载失败，HTTP {response.status_code}")
                    return
                with open(file_path, "wb") as f:
                    async for chunk in response.aiter_bytes():
                        f.write(chunk)
        except Exception as e:
            await smart_edit_text(msg, f"❌ 下载出错: {str(e)}")
            return

        # 3. 转换为 Markdown
        await smart_edit_text(msg, "🔄 正在转换为 Markdown...")
        md_filename = f"{os.path.splitext(os.path.basename(query) if is_direct_url else query)[0]}.md"
        # 清理文件名
        md_filename = re.sub(r'[\\/*?:"<>|]', "", md_filename) or "book.md"
        md_path = os.path.join(work_dir, md_filename)

        # 判断文件类型并转换
        # 这里简单判断是否为 zip (epub)
        if zipfile.is_zipfile(file_path):
            success = convert_epub_to_md(file_path, md_path)
        else:
            # 假设是纯文本
            try:
                os.rename(file_path, md_path)
                success = True
            except:
                success = False

        if success and os.path.exists(md_path):
            await smart_edit_text(msg, "✅ 转换完成，正在上传...")
            await update.message.reply_document(document=open(md_path, 'rb'), filename=md_filename)
            # 清理文件
            try:
                os.remove(file_path)
                os.remove(md_path)
            except:
                pass
        else:
            await smart_edit_text(msg, "❌ 转换失败，可能不是有效的 EPUB 格式。")