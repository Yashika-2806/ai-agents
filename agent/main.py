import asyncio
import json
import os
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import cloudscraper
import httpx
from httpx import RequestError
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from graphql import GraphQLSchema, GraphQLObjectType, GraphQLField, GraphQLString, GraphQLFloat, GraphQLInt, GraphQLList, GraphQLNonNull, graphql_sync

load_dotenv()

app = FastAPI(title="Profile Scraper")
static_dir = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    if isinstance(exc, HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    return JSONResponse(status_code=500, content={"detail": str(exc)})

@app.get("/", response_class=FileResponse)
async def root():
    return FileResponse(os.path.join(static_dir, "index.html"))

class ProfileQuery(BaseModel):
    leetcode: Optional[str] = None
    codeforces: Optional[str] = None
    codechef: Optional[str] = None
    hackerrank: Optional[str] = None
    atcoder: Optional[str] = None
    spoj: Optional[str] = None
    hackerearth: Optional[str] = None

from typing import Any, Dict, List, Optional, Union

class ScraperOutput(BaseModel):
    platform: str
    profile_url: str
    solved_count: Optional[int]
    rating: Optional[int]
    rank: Optional[Union[str, int]]
    percentile: Optional[float]
    contest_rating: Optional[int]
    problems_by_difficulty: Optional[Dict[str, int]]
    recent_status_distribution: Optional[Dict[str, int]]

class CodeEvaluationOutput(BaseModel):
    leetcode_percentile: Optional[float] = Field(default=None, description="Normalized global ranking")
    consistency_score: int = Field(ge=0, le=100, description="Platform consistency score")
    code_red_flags: List[str] = Field(description="Potential issues or low-signal patterns")

class ProfileResponse(BaseModel):
    profiles: List[ScraperOutput]
    evaluation: CodeEvaluationOutput

async def fetch_html(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.codeforces.com/",
    }
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        try:
            response = await client.get(url, headers=headers)
        except RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Error fetching {url}: {exc}") from exc

        if response.status_code == 403:
            raise HTTPException(status_code=502, detail=f"Forbidden fetching {url}: remote site blocked our request")
        if response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to fetch {url}: {response.status_code} {response.reason_phrase}",
            )

        return response.text

async def fetch_codeforces_profile_html(url: str) -> str:
    loop = asyncio.get_running_loop()
    def sync_fetch():
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://codeforces.com/",
        }

        try:
            scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "mobile": False})
            r = scraper.get(url, timeout=30, headers=headers)
            r.raise_for_status()
            return r.text
        except Exception as primary_exc:
            try:
                with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                    response = client.get(url, headers=headers)
                    if response.status_code == 200:
                        return response.text
                    primary_message = f"cloudscraper failed and direct fetch returned {response.status_code} {response.reason_phrase}"
            except Exception as fallback_exc:
                primary_message = f"cloudscraper failed ({primary_exc}) and direct fetch failed ({fallback_exc})"
            raise HTTPException(status_code=502, detail=f"Error fetching Codeforces profile HTML: {primary_message}") from primary_exc

    return await loop.run_in_executor(None, sync_fetch)

async def fetch_leetcode_profile(username: str) -> Dict[str, Any]:
    query = {
        "query": "query getUserProfile($username: String!) { matchedUser(username: $username) { username profile { ranking reputation countryName school company jobTitle websites } submitStats { acSubmissionNum { difficulty count submissions } totalSubmissionNum { difficulty count submissions } } } }",
        "variables": {"username": username},
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/json",
        "Referer": "https://leetcode.com/",
    }

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        try:
            response = await client.post("https://leetcode.com/graphql", json=query, headers=headers)
        except RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Error fetching LeetCode GraphQL for {username}: {exc}") from exc

        if response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Failed to fetch LeetCode GraphQL for {username}: {response.status_code} {response.reason_phrase}",
            )

        data = response.json()
        if "errors" in data:
            raise HTTPException(status_code=502, detail=f"LeetCode GraphQL error: {data['errors']}")
        return data.get("data", {}).get("matchedUser") or {}

async def scrape_leetcode(url: str) -> ScraperOutput:
    username_match = re.search(r"leetcode\.com/(?:u|profile)/([^/]+)/?", url)
    if not username_match:
        raise HTTPException(status_code=400, detail=f"Invalid LeetCode URL: {url}")

    username = username_match.group(1)
    profile_data = await fetch_leetcode_profile(username)
    problems_by_difficulty = {}
    solved = None

    submit_stats = profile_data.get("submitStats", {})
    ac_submissions = submit_stats.get("acSubmissionNum") or []
    for item in ac_submissions:
        difficulty = item.get("difficulty")
        count = item.get("count")
        if difficulty and count is not None:
            problems_by_difficulty[difficulty] = count

    if "All" in problems_by_difficulty:
        solved = problems_by_difficulty["All"]
    else:
        solved = sum(value for key, value in problems_by_difficulty.items() if key != "All")

    profile = profile_data.get("profile") or {}
    rank = profile.get("ranking")
    percentile = None
    if rank is not None:
        percentile = max(0.0, min(100.0, 100.0 - rank / 100000.0 * 100.0))

    return ScraperOutput(
        platform="LeetCode",
        profile_url=url,
        solved_count=solved,
        rating=None,
        rank=rank,
        percentile=round(percentile, 2) if percentile is not None else None,
        contest_rating=None,
        problems_by_difficulty=problems_by_difficulty if problems_by_difficulty else None,
        recent_status_distribution=None,
    )

async def fetch_codeforces_status(handle: str) -> Dict[str, Any]:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Referer": "https://codeforces.com/",
    }
    verdicts: Dict[str, int] = {}
    solved_problems = set()
    start = 1
    page_size = 1000

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            url = f"https://codeforces.com/api/user.status?handle={handle}&from={start}&count={page_size}"
            try:
                response = await client.get(url, headers=headers)
            except RequestError as exc:
                raise HTTPException(status_code=502, detail=f"Error fetching Codeforces submissions for {handle}: {exc}") from exc

            if response.status_code != 200:
                raise HTTPException(
                    status_code=502,
                    detail=f"Failed to fetch Codeforces submissions: {response.status_code} {response.reason_phrase}",
                )

            try:
                data = response.json()
            except ValueError as exc:
                raise HTTPException(status_code=502, detail="Invalid Codeforces submissions response") from exc

            if data.get("status") != "OK":
                raise HTTPException(status_code=502, detail=f"Codeforces API error: {data.get('comment')}")

            results = data.get("result", [])
            for item in results:
                verdict = item.get("verdict")
                if verdict:
                    verdicts[verdict] = verdicts.get(verdict, 0) + 1
                if verdict == "OK":
                    problem = item.get("problem", {})
                    contest_id = problem.get("contestId")
                    index = problem.get("index")
                    if contest_id and index:
                        solved_problems.add(f"{contest_id}-{index}")

            if len(results) < page_size:
                break

            start += page_size
            if start > 20000:
                break

    return {
        "verdict_distribution": verdicts,
        "solved_count": len(solved_problems),
    }

async def fetch_codeforces_user(handle: str) -> Dict[str, Any]:
    url = f"https://codeforces.com/api/user.info?handles={handle}"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://codeforces.com/",
    }
    
    loop = asyncio.get_running_loop()
    
    def fetch_with_cloudscraper():
        import time
        for attempt in range(3):
            try:
                scraper = cloudscraper.create_scraper()
                r = scraper.get(url, timeout=20, headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    if data.get("status") == "OK":
                        return data.get("result", [{}])[0]
                    return {}
                if attempt < 2:
                    time.sleep(1)
            except Exception:
                if attempt < 2:
                    time.sleep(1)
        return {}
    
    try:
        result = await loop.run_in_executor(None, fetch_with_cloudscraper)
        return result if result else {}
    except Exception:
        return {}


def parse_codeforces_profile_solved(html: str) -> Optional[int]:
    soup = BeautifulSoup(html, "html.parser")
    counter_blocks = soup.select("div._UserActivityFrame_counter")

    for block in counter_blocks:
        description_tag = block.select_one("div._UserActivityFrame_counterDescription")
        value_tag = block.select_one("div._UserActivityFrame_counterValue")
        if not description_tag or not value_tag:
            continue

        description = description_tag.get_text(strip=True).lower()
        if "solved for all time" in description or "solved all time" in description:
            solved_text = value_tag.get_text(strip=True)
            solved_match = re.search(r"([0-9,]+)", solved_text)
            if solved_match:
                return int(solved_match.group(1).replace(",", ""))

    # Broad fallback against normalized text.
    page_text = soup.get_text(separator=" ", strip=True)
    for pattern in [
        r"([0-9,]+)\s+problems\s+solved\s+for\s+all\s+time",
        r"([0-9,]+)\s+solved\s+for\s+all\s+time",
        r"([0-9,]+)\s+problems\s+solved",
    ]:
        generic_match = re.search(pattern, page_text, re.IGNORECASE)
        if generic_match:
            return int(generic_match.group(1).replace(",", ""))

    # Last fallback: raw HTML block matching.
    html_match = re.search(
        r"<div[^>]*class=[\"'][^\"']*_UserActivityFrame_counterValue[^\"']*[\"'][^>]*>.*?([0-9,]+).*?</div>.*?<div[^>]*class=[\"'][^\"']*_UserActivityFrame_counterDescription[^\"']*[\"'][^>]*>.*?solved\s*for\s*all\s*time.*?</div>",
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if html_match:
        return int(html_match.group(1).replace(",", ""))

    return None

async def scrape_codeforces(url: str) -> ScraperOutput:
    solved = None
    rating = None
    rank = None
    percentile = None
    contest_rating = None
    problems_by_difficulty = None
    recent_status_distribution = None

    handle_match = re.search(r"codeforces\.com/(?:profile/|submissions/)([^/]+)/?", url)
    if not handle_match:
        handle_match = re.search(r"codeforces\.com/([^/]+)/?", url)

    if not handle_match:
        raise HTTPException(status_code=400, detail=f"Invalid Codeforces URL: {url}")

    handle = handle_match.group(1)
    user_info = await fetch_codeforces_user(handle)

    rating = user_info.get("rating")
    contest_rating = user_info.get("maxRating")
    rank = user_info.get("maxRank") or user_info.get("rank")
    if rating is not None:
        percentile = max(0.0, min(100.0, 100.0 - rating / 3500.0 * 100.0))

    try:
        status_info = await fetch_codeforces_status(handle)
        recent_status_distribution = status_info.get("verdict_distribution")
        solved = status_info.get("solved_count")
    except Exception:
        recent_status_distribution = None

    # If the API-based solved count didn't work, try HTML scraping as fallback.
    if solved is None:
        try:
            profile_html = await fetch_codeforces_profile_html(f"https://codeforces.com/profile/{handle}")
            solved = parse_codeforces_profile_solved(profile_html)
        except Exception:
            solved = None

    return ScraperOutput(
        platform="Codeforces",
        profile_url=url,
        solved_count=solved,
        rating=rating,
        rank=rank,
        percentile=round(percentile, 2) if percentile is not None else None,
        contest_rating=contest_rating,
        problems_by_difficulty=None,
        recent_status_distribution=recent_status_distribution,
    )

async def scrape_codechef(url: str) -> ScraperOutput:
    html = await fetch_html(url)
    soup = BeautifulSoup(html, "lxml")
    solved = None
    rating = None
    rank = None
    percentile = None
    contest_rating = None

    rating_tag = soup.find("span", class_=re.compile(r"rating-number"))
    if rating_tag:
        rating = int(rating_tag.get_text(strip=True))

    solved_tag = soup.find(string=re.compile(r"Problems solved"))
    if solved_tag:
        solved_match = re.search(r"(\d+)\s+Problems solved", solved_tag)
        if solved_match:
            solved = int(solved_match.group(1))

    return ScraperOutput(
        platform="CodeChef",
        profile_url=url,
        solved_count=solved,
        rating=rating,
        rank=rank,
        percentile=percentile,
        contest_rating=contest_rating,
        problems_by_difficulty=None,
        recent_status_distribution=None,
    )

async def fetch_hackerrank_profile(username: str) -> Dict[str, Any]:
    url = f"https://www.hackerrank.com/rest/hackers/{username}/profile"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            response = await client.get(url, headers=headers)
        except RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Error fetching HackerRank profile for {username}: {exc}") from exc

        if response.status_code != 200:
            return {}

        return response.json()

async def scrape_hackerrank(url: str) -> ScraperOutput:
    username_match = re.search(r"hackerrank\.com/(?:profile/)?([^/]+)/?", url)
    if not username_match:
        raise HTTPException(status_code=400, detail=f"Invalid HackerRank URL: {url}")

    username = username_match.group(1)
    html = await fetch_html(url)
    soup = BeautifulSoup(html, "lxml")
    solved = None
    rating = None
    rank = None
    percentile = None
    contest_rating = None
    problems_by_difficulty = None
    recent_status_distribution = None

    solved_tag = soup.find(string=re.compile(r"Solved"))
    if solved_tag:
        solved_match = re.search(r"(\d+)\s+Solved", solved_tag)
        if solved_match:
            solved = int(solved_match.group(1))

    rating_tag = soup.find("div", class_=re.compile(r"score-card"))
    if rating_tag:
        rating_match = re.search(r"(\d+)", rating_tag.get_text())
        if rating_match:
            rating = int(rating_match.group(1))

    hackerrank_data = await fetch_hackerrank_profile(username)
    if hackerrank_data:
        stats = hackerrank_data.get("model", {}).get("statistics", {})
        if isinstance(stats, dict):
            recent_status_distribution = {
                entry.get("status", "Unknown"): entry.get("amount", 0)
                for entry in stats.get("tracks", [])
                if isinstance(entry, dict)
            }

    return ScraperOutput(
        platform="HackerRank",
        profile_url=url,
        solved_count=solved,
        rating=rating,
        rank=rank,
        percentile=percentile,
        contest_rating=contest_rating,
        problems_by_difficulty=problems_by_difficulty,
        recent_status_distribution=recent_status_distribution,
    )

async def scrape_atcoder(url: str) -> ScraperOutput:
    username_match = re.search(r"atcoder\.jp/users/([^/]+)/?", url)
    if not username_match:
        raise HTTPException(status_code=400, detail=f"Invalid AtCoder URL: {url}")

    username = username_match.group(1)
    html = await fetch_html(url)
    solved = None
    rating = None
    rank = None
    percentile = None
    problems_by_difficulty = None
    recent_status_distribution = None

    rating_match = re.search(r"Rating</th>[\s\S]*?([0-9]+)", html)
    if rating_match:
        rating = int(rating_match.group(1))

    rank_match = re.search(r"Rank</th>[\s\S]*?#([0-9,]+)", html)
    if rank_match:
        rank = int(rank_match.group(1).replace(",", ""))
        percentile = max(0.0, min(100.0, 100.0 - rank / 100000.0 * 100.0))

    solved_match = re.search(r"Problem Solved</th>[\s\S]*?<td>([0-9,]+)", html)
    if solved_match:
        solved = int(solved_match.group(1).replace(",", ""))

    return ScraperOutput(
        platform="AtCoder",
        profile_url=url,
        solved_count=solved,
        rating=rating,
        rank=rank,
        percentile=round(percentile, 2) if percentile is not None else None,
        contest_rating=None,
        problems_by_difficulty=problems_by_difficulty,
        recent_status_distribution=recent_status_distribution,
    )

async def scrape_spoj(url: str) -> ScraperOutput:
    username_match = re.search(r"spoj\.com/users/([^/]+)/?", url)
    if not username_match:
        raise HTTPException(status_code=400, detail=f"Invalid SPOJ URL: {url}")

    username = username_match.group(1)
    html = await fetch_html(url)
    solved = None
    rating = None
    rank = None
    percentile = None
    contest_rating = None
    problems_by_difficulty = None
    recent_status_distribution = None

    solved_match = re.search(r"Problems solved</td>\s*<td>([0-9,]+)", html)
    if solved_match:
        solved = int(solved_match.group(1).replace(",", ""))

    return ScraperOutput(
        platform="SPOJ",
        profile_url=url,
        solved_count=solved,
        rating=rating,
        rank=rank,
        percentile=percentile,
        contest_rating=contest_rating,
        problems_by_difficulty=problems_by_difficulty,
        recent_status_distribution=recent_status_distribution,
    )

async def scrape_hackerearth(url: str) -> ScraperOutput:
    username_match = re.search(r"hackerearth\.com/(?:user|profile)/([^/]+)/?", url)
    if not username_match:
        raise HTTPException(status_code=400, detail=f"Invalid HackerEarth URL: {url}")

    username = username_match.group(1)
    html = await fetch_html(url)
    solved = None
    rating = None
    rank = None
    percentile = None
    contest_rating = None
    problems_by_difficulty = None
    recent_status_distribution = None

    solved_match = re.search(r"Problems solved</div>[\s\S]*?<div class=\"stat-value\">([0-9,]+)", html)
    if solved_match:
        solved = int(solved_match.group(1).replace(",", ""))

    return ScraperOutput(
        platform="HackerEarth",
        profile_url=url,
        solved_count=solved,
        rating=rating,
        rank=rank,
        percentile=percentile,
        contest_rating=contest_rating,
        problems_by_difficulty=problems_by_difficulty,
        recent_status_distribution=recent_status_distribution,
    )

async def evaluate_code_metrics(profiles: List[ScraperOutput]) -> CodeEvaluationOutput:
    leetcode_percentile: Optional[float] = None
    consistency_score = 0
    code_red_flags = []

    profile_count = len(profiles)

    for profile in profiles:
        if profile.platform == "LeetCode" and profile.rank is not None:
            percentile = max(0.0, min(100.0, 100.0 - profile.rank / 100000.0 * 100.0))
            leetcode_percentile = round(percentile, 2)

    consistency_score = min(100, profile_count * 15)

    if profile_count == 0:
        code_red_flags.append("No coding profiles provided.")
    if leetcode_percentile is not None and leetcode_percentile < 20:
        code_red_flags.append("Low LeetCode percentile may indicate less algorithm practice.")

    return CodeEvaluationOutput(
        leetcode_percentile=leetcode_percentile,
        consistency_score=consistency_score,
        code_red_flags=code_red_flags,
    )

@app.post("/analyze", response_model=ProfileResponse)
async def analyze_profiles(request: Request):
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="Request body must be valid JSON")

    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Request JSON must be an object")

    query = ProfileQuery(
        leetcode=payload.get("leetcode"),
        codeforces=payload.get("codeforces"),
        codechef=payload.get("codechef"),
        hackerrank=payload.get("hackerrank"),
        atcoder=payload.get("atcoder"),
        spoj=payload.get("spoj"),
        hackerearth=payload.get("hackerearth"),
    )

    profiles: List[ScraperOutput] = []

    if query.leetcode:
        profiles.append(await scrape_leetcode(str(query.leetcode)))
    if query.codeforces:
        profiles.append(await scrape_codeforces(str(query.codeforces)))
    if query.codechef:
        profiles.append(await scrape_codechef(str(query.codechef)))
    if query.hackerrank:
        profiles.append(await scrape_hackerrank(str(query.hackerrank)))
    if query.atcoder:
        profiles.append(await scrape_atcoder(str(query.atcoder)))
    if query.spoj:
        profiles.append(await scrape_spoj(str(query.spoj)))
    if query.hackerearth:
        profiles.append(await scrape_hackerearth(str(query.hackerearth)))

    if not profiles:
        raise HTTPException(status_code=400, detail="At least one profile URL is required")

    evaluation = await evaluate_code_metrics(profiles)
    return ProfileResponse(profiles=profiles, evaluation=evaluation)

schema = GraphQLSchema(
    query=GraphQLObjectType(
        name="Query",
        fields={
            "profiles": GraphQLField(
                GraphQLList(GraphQLNonNull(GraphQLString)),
                resolve=lambda obj, info: [],
            ),
        },
    )
)

@app.post("/graphql")
async def graphql_endpoint(body: Dict[str, Any]):
    query = body.get("query")
    if not query:
        raise HTTPException(status_code=400, detail="GraphQL query missing")
    result = graphql_sync(schema, query)
    if result.errors:
        raise HTTPException(status_code=400, detail=[str(e) for e in result.errors])
    return result.data

@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)
