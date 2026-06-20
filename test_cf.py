import asyncio
from agent.main import fetch_codeforces_profile_html, parse_codeforces_profile_solved, fetch_codeforces_status

async def test():
    handle = "Amritasingh2904"
    try:
        html = await fetch_codeforces_profile_html(f"https://codeforces.com/profile/{handle}")
        solved = parse_codeforces_profile_solved(html)
        print(f"HTML solved: {solved}")
    except Exception as e:
        print(f"HTML error: {e}")

    try:
        status = await fetch_codeforces_status(handle)
        print(f"API solved: {status.get('solved_count')}")
    except Exception as e:
        print(f"API error: {e}")

asyncio.run(test())
