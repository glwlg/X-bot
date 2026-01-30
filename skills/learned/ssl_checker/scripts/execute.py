"""
SSL 证书查询 Skill
"""
import ssl
import socket
from datetime import datetime, timezone
from core.platform.models import UnifiedContext
from utils import smart_reply_text


async def execute(ctx: UnifiedContext, params: dict) -> str:
    """查询域名 SSL 证书信息"""
    domain = params.get("domain", "651971564.xyz")
    port = params.get("port", 443)
    
    # 清理域名格式
    domain = domain.replace("https://", "").replace("http://", "").split("/")[0]
    
        # 创建 SSL 上下文
        ssl_context = ssl.create_default_context()
        
        # 连接并获取证书
        with socket.create_connection((domain, port), timeout=10) as sock:
            with ssl_context.wrap_socket(sock, server_hostname=domain) as ssock:
                cert = ssock.getpeercert()
        
        # 解析证书信息
        not_after_str = cert.get("notAfter", "")
        not_before_str = cert.get("notBefore", "")
        issuer = dict(x[0] for x in cert.get("issuer", []))
        subject = dict(x[0] for x in cert.get("subject", []))
        
        # 解析日期 (格式: 'Mar 15 12:00:00 2025 GMT')
        not_after = datetime.strptime(not_after_str, "%b %d %H:%M:%S %Y %Z")
        not_after = not_after.replace(tzinfo=timezone.utc)
        
        # 计算剩余天数
        now = datetime.now(timezone.utc)
        days_left = (not_after - now).days
        
        # 确定状态
        if days_left < 0:
            status = "❌ 已过期"
        elif days_left <= 7:
            status = "🔴 即将到期（7天内）"
        elif days_left <= 30:
            status = "🟡 即将到期（30天内）"
        else:
            status = "🟢 正常"
        
        # 构建消息
        issuer_name = issuer.get("organizationName", issuer.get("commonName", "未知"))
        common_name = subject.get("commonName", domain)
        
        message = (
            f"🔐 **SSL 证书信息**\n\n"
            f"**域名**: {domain}\n"
            f"**证书主体**: {common_name}\n"
            f"**颁发者**: {issuer_name}\n"
            f"**到期时间**: {not_after.strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
            f"**剩余天数**: {days_left} 天\n"
            f"**状态**: {status}"
        )
        
        await ctx.reply(message)
        
        return f"SSL证书查询结果: 域名 {domain} 的证书将于 {not_after.strftime('%Y-%m-%d')} 到期，剩余 {days_left} 天，状态: {status}"
        
    except socket.timeout:
        error_msg = f"❌ 连接超时: 无法连接到 {domain}:{port}"
        await ctx.reply(error_msg)
        return f"Error: 连接 {domain} 超时"
        
    except socket.gaierror:
        error_msg = f"❌ 域名解析失败: {domain} 可能不存在"
        await ctx.reply(error_msg)
        return f"Error: 域名 {domain} 解析失败"
        
    except ssl.SSLCertVerificationError as e:
        error_msg = f"❌ SSL 证书验证失败: {str(e)}"
        await ctx.reply(error_msg)
        return f"Error: SSL证书验证失败 - {str(e)}"
        
    except Exception as e:
        error_msg = f"❌ 查询失败: {str(e)}"
        await ctx.reply(error_msg)
        return f"Error: {str(e)}"
