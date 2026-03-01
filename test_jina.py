import asyncio
import httpx
async def fetch():
    jina_url = "https://r.jina.ai/https://www.qweather.com/weather/wuxi-101190201.html"
    try:
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            resp = await client.get(
                jina_url,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "text/markdown"}
            )
            print("Status:", resp.status_code)
            print("Content length:", len(resp.text))
            print("Preview:", resp.text[:200])
    except Exception as e:
        print("Error:", repr(e))
asyncio.run(fetch())
