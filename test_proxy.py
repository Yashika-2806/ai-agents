import asyncio
import httpx
from agent.main import parse_codeforces_profile_solved

async def test_proxy():
    handle = "Amritasingh2904"
    urls = [
        f"https://corsproxy.io/?https://codeforces.com/profile/{handle}",
        f"https://api.allorigins.win/raw?url=https://codeforces.com/profile/{handle}",
    ]
    
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for url in urls:
            try:
                res = await client.get(url)
                solved = parse_codeforces_profile_solved(res.text)
                print(f"URL: {url}")
                print(f"Solved: {solved}")
                print("-" * 20)
            except Exception as e:
                print(f"Failed for {url}: {e}")

asyncio.run(test_proxy())
