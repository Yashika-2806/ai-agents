import asyncio
import json
import math
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

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

app = FastAPI(title="CP-Agent Profile Scorer")
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

# ─── Pydantic Models ─────────────────────────────────────────────────────────

class ProfileQuery(BaseModel):
    student_name: Optional[str] = None
    leetcode: Optional[str] = None
    codeforces: Optional[str] = None
    codechef: Optional[str] = None
    hackerrank: Optional[str] = None

class ScraperOutput(BaseModel):
    platform: str
    profile_url: str
    solved_count: Optional[int] = None
    rating: Optional[int] = None
    rank: Optional[Union[str, int]] = None
    percentile: Optional[float] = None
    contest_rating: Optional[int] = None
    problems_by_difficulty: Optional[Dict[str, int]] = None
    recent_status_distribution: Optional[Dict[str, int]] = None
    # Extended fields used by scoring engine
    extra: Optional[Dict[str, Any]] = None

class SubScoreExplanation(BaseModel):
    """Explainability details for a single sub-score."""
    raw_value: Optional[float] = None
    score: float
    formula: str
    reasoning: str

class PlatformScore(BaseModel):
    platform: str
    weight: float                     # Dynamic weight assigned to this platform
    clout: SubScoreExplanation
    consistency: SubScoreExplanation
    velocity: SubScoreExplanation
    platform_score: float             # Weighted combination of clout/consistency/velocity
    reasoning: str

class ScoringResult(BaseModel):
    platform_scores: List[PlatformScore]
    final_score: float
    score_tier: str                  # e.g. "Elite Coder", "Advanced", etc.
    aggregation_method: str
    overall_reasoning: str

class CodeEvaluationOutput(BaseModel):
    leetcode_percentile: Optional[float] = Field(default=None)
    consistency_score: int = Field(ge=0, le=100)
    code_red_flags: List[str] = Field(default_factory=list)

class ScoreBreakdown(BaseModel):
    overall_score: int = Field(ge=0, le=100)
    dsa_strength: str
    competitive_programming: str
    open_source: str
    interview_readiness: str
    faang_readiness: str

class SkillsRadar(BaseModel):
    dsa: int = Field(ge=0, le=100)
    cp: int = Field(ge=0, le=100)
    open_source: int = Field(ge=0, le=100)
    consistency: int = Field(ge=0, le=100)
    interview: int = Field(ge=0, le=100)

class AIAnalysis(BaseModel):
    strengths: List[str]
    weaknesses: List[str]
    recommended_topics: List[str]
    next_steps: List[str]
    personalized_feedback: str

class ProfileResponse(BaseModel):
    profiles: List[ScraperOutput]
    evaluation: CodeEvaluationOutput
    scores: ScoreBreakdown
    radar: SkillsRadar
    analysis: AIAnalysis
    cp_scoring: Optional[ScoringResult] = None

# ─── HTTP Helpers ──────────────────────────────────────────────────────────────

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
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://codeforces.com/",
    }
    try:
        from curl_cffi import requests as cffi_requests
        async with cffi_requests.AsyncSession(impersonate="chrome110") as session:
            response = await session.get(url, headers=headers, timeout=30)
            if response.status_code == 200:
                return response.text
            raise HTTPException(status_code=502, detail=f"curl_cffi failed with {response.status_code}")
    except ImportError:
        pass
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Error fetching CF profile: {exc}")

    loop = asyncio.get_running_loop()
    def sync_fetch():
        try:
            scraper = cloudscraper.create_scraper(browser={"browser": "chrome", "platform": "windows", "mobile": False})
            r = scraper.get(url, timeout=30, headers=headers)
            r.raise_for_status()
            return r.text
        except Exception as primary_exc:
            raise HTTPException(status_code=502, detail=f"cloudscraper failed ({primary_exc})") from primary_exc
    return await loop.run_in_executor(None, sync_fetch)

# ─── LeetCode Scraper ─────────────────────────────────────────────────────────

async def fetch_leetcode_profile(username: str) -> Dict[str, Any]:
    # Fetch main profile data
    query = {
        "query": """query getUserProfile($username: String!) {
          matchedUser(username: $username) {
            username
            profile { ranking reputation countryName }
            submitStats {
              acSubmissionNum { difficulty count submissions }
              totalSubmissionNum { difficulty count submissions }
            }
            userCalendar {
              submissionCalendar
            }
          }
        }""",
        "variables": {"username": username},
    }
    # Also fetch contest data
    contest_query = {
        "query": """query userContestRankingInfo($username: String!) {
          userContestRanking(username: $username) {
            attendedContestsCount
            rating
            globalRanking
            totalParticipants
            topPercentage
          }
          userContestRankingHistory(username: $username) {
            attended
            rating
            ranking
            contest { startTime }
          }
        }""",
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
            r1 = await client.post("https://leetcode.com/graphql", json=query, headers=headers)
            r2 = await client.post("https://leetcode.com/graphql", json=contest_query, headers=headers)
        except RequestError as exc:
            raise HTTPException(status_code=502, detail=f"Error fetching LeetCode GraphQL for {username}: {exc}") from exc

        profile_data = r1.json().get("data", {}).get("matchedUser") or {} if r1.status_code == 200 else {}
        contest_data = r2.json().get("data", {}) if r2.status_code == 200 else {}
        return {"profile": profile_data, "contest": contest_data}

async def scrape_leetcode(url: str) -> ScraperOutput:
    username_match = re.search(r"leetcode\.com/(?:u|profile)/([^/]+)/?", url)
    if not username_match:
        raise HTTPException(status_code=400, detail=f"Invalid LeetCode URL: {url}")

    username = username_match.group(1)
    raw = await fetch_leetcode_profile(username)
    profile_data = raw.get("profile", {})
    contest_data = raw.get("contest", {})

    problems_by_difficulty = {}
    ac_submissions = (profile_data.get("submitStats") or {}).get("acSubmissionNum") or []
    total_submissions_list = (profile_data.get("submitStats") or {}).get("totalSubmissionNum") or []
    
    total_accepted = 0
    total_attempted = 0
    for item in ac_submissions:
        d, c = item.get("difficulty"), item.get("count", 0)
        if d:
            problems_by_difficulty[d] = c
            if d != "All":
                total_accepted += item.get("submissions", c)
    for item in total_submissions_list:
        if item.get("difficulty") == "All":
            total_attempted = item.get("submissions", 0)
    
    solved = problems_by_difficulty.get("All") or sum(v for k, v in problems_by_difficulty.items() if k != "All")
    hard_solved = problems_by_difficulty.get("Hard", 0)

    profile = profile_data.get("profile") or {}
    rank = profile.get("ranking")
    percentile = None
    if rank is not None and rank > 0:
        percentile = max(0.0, min(100.0, 100.0 - rank / 100000.0 * 100.0))

    # Parse practice submission calendar
    monthly_practice = [0] * 12
    calendar_str = (profile_data.get("userCalendar") or {}).get("submissionCalendar")
    if calendar_str:
        try:
            import json
            cal = json.loads(calendar_str)
            now = datetime.now(timezone.utc)
            for ts_str, count in cal.items():
                ts = int(ts_str)
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                months_ago = (now.year - dt.year) * 12 + (now.month - dt.month)
                if 0 <= months_ago < 12:
                    monthly_practice[11 - months_ago] += count
        except Exception:
            pass

    # Contest info
    contest_ranking = contest_data.get("userContestRanking") or {}
    contest_rating = int(contest_ranking.get("rating") or 0) or None
    global_rank = contest_ranking.get("globalRanking")
    total_participants = contest_ranking.get("totalParticipants", 1000000)
    contests_attended = contest_ranking.get("attendedContestsCount", 0)

    # Build 12-month submission calendar from contest history
    history = contest_data.get("userContestRankingHistory") or []
    now = datetime.now(timezone.utc)
    monthly_counts = [0] * 12
    for entry in history:
        if not entry.get("attended"):
            continue
        ts = entry.get("contest", {}).get("startTime")
        if ts:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            months_ago = (now.year - dt.year) * 12 + (now.month - dt.month)
            if 0 <= months_ago < 12:
                monthly_counts[11 - months_ago] += 1

    return ScraperOutput(
        platform="LeetCode",
        profile_url=url,
        solved_count=solved,
        rating=None,
        rank=rank,
        percentile=round(percentile, 2) if percentile is not None else None,
        contest_rating=contest_rating,
        problems_by_difficulty=problems_by_difficulty if problems_by_difficulty else None,
        recent_status_distribution=None,
        extra={
            "hard_solved": hard_solved,
            "total_accepted": total_accepted,
            "total_attempted": total_attempted,
            "global_rank": global_rank,
            "total_participants": total_participants,
            "contests_attended": contests_attended,
            "monthly_contest_counts": monthly_counts,
            "monthly_practice_counts": monthly_practice,
        }
    )

# ─── Codeforces Scraper ───────────────────────────────────────────────────────

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
    wrong_during_contest = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            url = f"https://codeforces.com/api/user.status?handle={handle}&from={start}&count={page_size}"
            try:
                response = await client.get(url, headers=headers)
            except RequestError as exc:
                raise HTTPException(status_code=502, detail=f"Error fetching Codeforces submissions for {handle}: {exc}") from exc

            if response.status_code != 200:
                raise HTTPException(status_code=502, detail=f"Failed to fetch Codeforces submissions: {response.status_code}")

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
                # Count wrong answers during contests (for velocity penalty)
                if verdict and verdict not in ("OK", "COMPILATION_ERROR") and item.get("author", {}).get("participantType") == "CONTESTANT":
                    wrong_during_contest += 1
                if verdict == "OK":
                    problem = item.get("problem", {})
                    contest_id = problem.get("contestId")
                    index = problem.get("index")
                    name = problem.get("name", "")
                    if contest_id is not None and index:
                        key = f"{contest_id}-{index}"
                    elif name:
                        key = f"name-{name}"
                    else:
                        continue
                    solved_problems.add(key)

            if len(results) < page_size:
                break
            start += page_size
            if start > 20000:
                break

    # Calculate active practice days in last 90 days
    now_ts = datetime.now(timezone.utc).timestamp()
    active_days_90 = set()
    for item in results:
        creation_time = item.get("creationTimeSeconds")
        if creation_time and (now_ts - creation_time) <= 90 * 86400:
            dt = datetime.fromtimestamp(creation_time, tz=timezone.utc)
            active_days_90.add(dt.strftime("%Y-%m-%d"))

    return {
        "verdict_distribution": verdicts,
        "solved_count": len(solved_problems),
        "wrong_during_contest": wrong_during_contest,
        "active_days_90": len(active_days_90),
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

async def fetch_codeforces_contest_history(handle: str) -> List[Dict]:
    """Fetch recent contest history to assess consistency (last 90 days)."""
    url = f"https://codeforces.com/api/user.rating?handle={handle}"
    headers = {"Accept": "application/json", "User-Agent": "Mozilla/5.0"}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url, headers=headers)
            if r.status_code == 200:
                data = r.json()
                if data.get("status") == "OK":
                    return data.get("result", [])
    except Exception:
        pass
    return []

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
    page_text = soup.get_text(separator=" ", strip=True)
    for pattern in [
        r"([0-9,]+)\s+problems\s+solved\s+for\s+all\s+time",
        r"([0-9,]+)\s+solved\s+for\s+all\s+time",
        r"([0-9,]+)\s+problems\s+solved",
    ]:
        generic_match = re.search(pattern, page_text, re.IGNORECASE)
        if generic_match:
            return int(generic_match.group(1).replace(",", ""))
    return None

async def scrape_codeforces(url: str) -> ScraperOutput:
    handle_match = re.search(r"codeforces\.com/(?:profile/|submissions/)([^/]+)/?", url)
    if not handle_match:
        handle_match = re.search(r"codeforces\.com/([^/]+)/?", url)
    if not handle_match:
        raise HTTPException(status_code=400, detail=f"Invalid Codeforces URL: {url}")

    handle = handle_match.group(1)
    user_info = await fetch_codeforces_user(handle)
    rating = user_info.get("rating")
    max_rating = user_info.get("maxRating")
    rank = user_info.get("maxRank") or user_info.get("rank")
    percentile = None
    if rating is not None:
        percentile = max(0.0, min(100.0, 100.0 - rating / 3500.0 * 100.0))

    api_solved = None
    html_solved = None
    status_info = {}
    wrong_during_contest = 0

    try:
        status_info = await fetch_codeforces_status(handle)
        api_solved = status_info.get("solved_count")
        wrong_during_contest = status_info.get("wrong_during_contest", 0)
    except Exception:
        pass

    try:
        profile_html = await fetch_codeforces_profile_html(f"https://codeforces.com/profile/{handle}")
        html_solved = parse_codeforces_profile_solved(profile_html)
    except Exception:
        pass

    candidates = [c for c in [api_solved, html_solved] if c is not None]
    solved = max(candidates) if candidates else None

    # Fetch contest history for consistency scoring
    contest_history = await fetch_codeforces_contest_history(handle)
    now_ts = datetime.now(timezone.utc).timestamp()
    contests_last_90 = sum(
        1 for c in contest_history
        if (now_ts - c.get("ratingUpdateTimeSeconds", 0)) <= 90 * 86400
    )

    return ScraperOutput(
        platform="Codeforces",
        profile_url=url,
        solved_count=solved,
        rating=rating,
        rank=rank,
        percentile=round(percentile, 2) if percentile is not None else None,
        contest_rating=max_rating,
        problems_by_difficulty=None,
        recent_status_distribution=status_info.get("verdict_distribution"),
        extra={
            "max_rating": max_rating,
            "contests_last_90": contests_last_90,
            "wrong_during_contest": wrong_during_contest,
            "total_contest_count": len(contest_history),
            "active_days_90": status_info.get("active_days_90", 0),
        }
    )

# ─── CodeChef Scraper ─────────────────────────────────────────────────────────

async def scrape_codechef(url: str) -> ScraperOutput:
    html = await fetch_html(url)
    soup = BeautifulSoup(html, "lxml")
    solved = None
    rating = None
    rank = None
    stars = None
    global_rank = None

    rating_tag = soup.find("div", class_=re.compile(r"rating-number"))
    if rating_tag:
        try:
            rating = int(rating_tag.get_text(strip=True))
        except ValueError:
            pass

    # Stars from star icons
    star_section = soup.find("div", class_=re.compile(r"rating-star|stars"))
    if star_section:
        stars_text = star_section.get_text(strip=True)
        star_match = re.search(r"(\d+)\s*[★⭐]", stars_text)
        if star_match:
            stars = int(star_match.group(1))

    if stars is None and rating is not None:
        if rating >= 2500: stars = 7
        elif rating >= 2200: stars = 6
        elif rating >= 2000: stars = 5
        elif rating >= 1800: stars = 4
        elif rating >= 1600: stars = 3
        elif rating >= 1400: stars = 2
        else: stars = 1

    # Global rank
    rank_section = soup.find(string=re.compile(r"Global Rank", re.IGNORECASE))
    if rank_section and rank_section.parent:
        rank_parent = rank_section.parent.parent if rank_section.parent.parent else rank_section.parent
        rank_match = re.search(r"([\d,]+)", rank_parent.get_text())
        if rank_match:
            global_rank = int(rank_match.group(1).replace(",", ""))

    # Solved count
    solved_tag = soup.find(string=re.compile(r"Problems solved", re.IGNORECASE))
    if solved_tag:
        solved_match = re.search(r"(\d+)", solved_tag)
        if solved_match:
            solved = int(solved_match.group(1))

    # Fully/Partially solved from tags
    fully_solved = None
    partially_solved = None
    fully_match = re.search(r"(\d+)\s*Fully\s*Solved", html, re.IGNORECASE)
    partially_match = re.search(r"(\d+)\s*Partially\s*Solved", html, re.IGNORECASE)
    if fully_match:
        fully_solved = int(fully_match.group(1))
    if partially_match:
        partially_solved = int(partially_match.group(1))

    return ScraperOutput(
        platform="CodeChef",
        profile_url=url,
        solved_count=solved or fully_solved,
        rating=rating,
        rank=global_rank,
        percentile=None,
        contest_rating=rating,
        problems_by_difficulty=None,
        recent_status_distribution=None,
        extra={
            "stars": stars,
            "global_rank": global_rank,
            "fully_solved": fully_solved,
            "partially_solved": partially_solved,
        }
    )

# ─── HackerRank Scraper ───────────────────────────────────────────────────────

async def fetch_hackerrank_profile(username: str) -> Dict[str, Any]:
    url = f"https://www.hackerrank.com/rest/hackers/{username}"
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

async def fetch_hackerrank_badges(username: str) -> Dict[str, Any]:
    url = f"https://www.hackerrank.com/rest/hackers/{username}/badges"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            response = await client.get(url, headers=headers)
        except RequestError as exc:
            return {}
        if response.status_code != 200:
            return {}
        return response.json()

async def fetch_hackerrank_scores(username: str) -> list:
    url = f"https://www.hackerrank.com/rest/hackers/{username}/scores"
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        try:
            response = await client.get(url, headers=headers)
        except RequestError as exc:
            return []
        if response.status_code != 200:
            return []
        try:
            return response.json()
        except Exception:
            return []

async def scrape_hackerrank(url: str) -> ScraperOutput:
    username_match = re.search(r"hackerrank\.com/(?:profile/)?([^/]+)/?", url)
    if not username_match:
        raise HTTPException(status_code=400, detail=f"Invalid HackerRank URL: {url}")
    username = username_match.group(1)

    hackerrank_data, badges_data, scores_data = await asyncio.gather(
        fetch_hackerrank_profile(username),
        fetch_hackerrank_badges(username),
        fetch_hackerrank_scores(username)
    )
    
    total_score = 0.0
    badge_stars: List[int] = []
    perfect_challenges = 0
    created_at = None
    solved = 0

    if hackerrank_data:
        model = hackerrank_data.get("model", {})
        created_at = model.get("created_at")

    if badges_data:
        models = badges_data.get("models") or []
        for badge in models:
            stars = badge.get("stars", 0)
            badge_stars.append(stars)
            solved += badge.get("solved", 0)
            if stars > 0 and stars == badge.get("total_stars"):
                perfect_challenges += 1

    if scores_data:
        for track in scores_data:
            total_score += track.get("practice", {}).get("score", 0.0)

    if solved == 0 and total_score > 0:
        solved = int(total_score // 10) or 1
    if perfect_challenges == 0 and solved > 0:
        perfect_challenges = max(1, solved // 5)

    return ScraperOutput(
        platform="HackerRank",
        profile_url=url,
        solved_count=solved if solved > 0 else None,
        rating=None,
        rank=None,
        percentile=None,
        contest_rating=None,
        problems_by_difficulty=None,
        recent_status_distribution=None,
        extra={
            "total_score": total_score,
            "badge_stars": badge_stars,
            "perfect_challenges": perfect_challenges,
            "created_at": created_at,
        }
    )

# ─── GeeksForGeeks Scraper ────────────────────────────────────────────────────

async def fetch_gfg_profile(username: str) -> Dict[str, Any]:
    """
    GFG uses Next.js SSR — some profiles have data embedded in __next_f.push script tags.
    We extract the JSON payload using regex on the raw HTML.
    """
    url = f"https://www.geeksforgeeks.org/profile/{username}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.geeksforgeeks.org/",
    }
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        try:
            r = await client.get(url, headers=headers)
        except RequestError as exc:
            raise HTTPException(status_code=502, detail=f"GFG fetch error: {exc}")
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"GFG returned {r.status_code}")
        html = r.text

    # Extract all self.__next_f.push([...]) payloads
    payloads = re.findall(r'self\.__next_f\.push\(\[1\s*,\s*"(.*?)"\]\)', html, re.DOTALL)
    
    combined = " ".join(payloads)
    # Unescape JSON-encoded string
    try:
        combined = combined.encode('utf-8').decode('unicode_escape')
    except Exception:
        pass

    def extract_int(key: str) -> Optional[int]:
        m = re.search(rf'"{key}"\\?:\s*(-?\d+)', combined)
        return int(m.group(1)) if m else None

    def extract_float(key: str) -> Optional[float]:
        m = re.search(rf'"{key}"\\?:\s*(-?[\d.]+)', combined)
        return float(m.group(1)) if m else None

    def extract_str(key: str) -> Optional[str]:
        m = re.search(rf'"{key}"\\?:\s*"([^"]*)"', combined)
        return m.group(1) if m else None

    # Parse all known GFG fields
    data = {
        "score": extract_int("score"),
        "monthly_score": extract_int("monthly_score"),
        "total_problems_solved": extract_int("total_problems_solved"),
        "pod_solved_current_streak": extract_int("pod_solved_current_streak"),
        "pod_solved_longest_streak": extract_int("pod_solved_longest_streak"),
        "pod_solved_global_longest_streak": extract_int("pod_solved_global_longest_streak"),
        "pod_correct_submissions_count": extract_int("pod_correct_submissions_count"),
        "pod_solved_current_streak_incl_timemachine": extract_int("pod_solved_current_streak_incl_timemachine"),
        "created_date": extract_str("created_date"),
        "institute_rank": extract_str("institute_rank"),
    }

    # Calculate days active from created_date
    if data["created_date"]:
        try:
            created = datetime.strptime(data["created_date"][:10], "%Y-%m-%d")
            data["days_active"] = (datetime.now() - created).days
        except Exception:
            data["days_active"] = None
    else:
        data["days_active"] = None

    return data


async def scrape_geeksforgeeks(url: str) -> ScraperOutput:
    username_match = re.search(r"geeksforgeeks\.org/(?:user|profile)/([^/]+)/?", url)
    if not username_match:
        raise HTTPException(status_code=400, detail=f"Invalid GeeksForGeeks URL: {url}")
    username = username_match.group(1)

    data = await fetch_gfg_profile(username)

    solved = data.get("total_problems_solved")
    score = data.get("score")
    current_streak = data.get("pod_solved_current_streak") or 0
    longest_streak = data.get("pod_solved_longest_streak") or 0
    days_active = data.get("days_active")
    correct_submissions = data.get("pod_correct_submissions_count") or 0

    # Accuracy: correct POTD submissions / days_active
    accuracy = None
    if days_active and days_active > 0 and correct_submissions is not None:
        accuracy = min(100.0, (correct_submissions / max(days_active, 1)) * 100)

    return ScraperOutput(
        platform="GeeksForGeeks",
        profile_url=url,
        solved_count=solved,
        rating=score,
        rank=data.get("institute_rank"),
        percentile=None,
        contest_rating=None,
        problems_by_difficulty=None,
        recent_status_distribution=None,
        extra={
            "coding_score": score,
            "current_potd_streak": current_streak,
            "longest_potd_streak": longest_streak,
            "days_active": days_active,
            "accuracy": accuracy,
            "monthly_score": data.get("monthly_score"),
        }
    )

# ─── AtCoder, SPOJ, HackerEarth Scrapers (unchanged) ─────────────────────────

async def scrape_atcoder(url: str) -> ScraperOutput:
    username_match = re.search(r"atcoder\.jp/users/([^/]+)/?", url)
    if not username_match:
        raise HTTPException(status_code=400, detail=f"Invalid AtCoder URL: {url}")
    username = username_match.group(1)
    html = await fetch_html(url)
    solved = rating = rank = percentile = None
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
        platform="AtCoder", profile_url=url, solved_count=solved, rating=rating,
        rank=rank, percentile=round(percentile, 2) if percentile else None,
        contest_rating=None, problems_by_difficulty=None, recent_status_distribution=None,
    )

async def scrape_spoj(url: str) -> ScraperOutput:
    username_match = re.search(r"spoj\.com/users/([^/]+)/?", url)
    if not username_match:
        raise HTTPException(status_code=400, detail=f"Invalid SPOJ URL: {url}")
    html = await fetch_html(url)
    solved = None
    solved_match = re.search(r"Problems solved</td>\s*<td>([0-9,]+)", html)
    if solved_match:
        solved = int(solved_match.group(1).replace(",", ""))
    return ScraperOutput(
        platform="SPOJ", profile_url=url, solved_count=solved, rating=None, rank=None,
        percentile=None, contest_rating=None, problems_by_difficulty=None,
        recent_status_distribution=None,
    )

async def scrape_hackerearth(url: str) -> ScraperOutput:
    username_match = re.search(r"hackerearth\.com/(?:user|profile)/([^/]+)/?", url)
    if not username_match:
        raise HTTPException(status_code=400, detail=f"Invalid HackerEarth URL: {url}")
    html = await fetch_html(url)
    solved = None
    solved_match = re.search(r"Problems solved</div>[\s\S]*?<div class=\"stat-value\">([0-9,]+)", html)
    if solved_match:
        solved = int(solved_match.group(1).replace(",", ""))
    return ScraperOutput(
        platform="HackerEarth", profile_url=url, solved_count=solved, rating=None, rank=None,
        percentile=None, contest_rating=None, problems_by_difficulty=None,
        recent_status_distribution=None,
    )

# ─── CP-Agent Scoring Engine ──────────────────────────────────────────────────
# Formulas based on the CP-Agent specification
# Each platform: Clout (40%) + Consistency (30%) + Velocity (30%) = Platform Score
# Final Score = Σ(weight_i × platform_score_i) with dynamic weight allocation

PLATFORM_WEIGHTS = {
    "LeetCode": 0.40,
    "Codeforces": 0.30,
    "CodeChef": 0.20,
    "HackerRank": 0.10,
}

def clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))

def compute_consistency_sigma(monthly: List[int]) -> float:
    """Compute coefficient of variation of 12-month submission array."""
    if not monthly or len(monthly) == 0:
        return 0.0
    mu = sum(monthly) / len(monthly)
    if mu == 0:
        return 0.0
    variance = sum((x - mu) ** 2 for x in monthly) / len(monthly)
    sigma = math.sqrt(variance)
    return sigma

def score_leetcode(profile: ScraperOutput) -> PlatformScore:
    extra = profile.extra or {}
    
    # ── CLOUT ──
    # Contest Rating (0..3000 → 0..50) + Global Rank contribution (0..30) + Hard Solved (0..20)
    contest_rating = profile.contest_rating or 0
    hard_solved = extra.get("hard_solved", 0) or 0
    global_rank = extra.get("global_rank") or profile.rank or None
    total_participants = extra.get("total_participants")
    if not total_participants:
        total_participants = 1000000 if extra.get("global_rank") else 3000000

    clout_rating = clamp(contest_rating / 3000 * 50)
    
    if global_rank and total_participants > 0:
        rank_pct = global_rank / total_participants
        clout_rank = clamp((1 - rank_pct) * 30)
    else:
        clout_rank = 0.0

    clout_hard = clamp(hard_solved / 50 * 20)  # 50 hard = full 20 pts
    clout_val = clamp(clout_rating + clout_rank + clout_hard)

    clout_reason = (
        f"Contest rating {contest_rating} → {clout_rating:.1f}/50 pts. "
        f"Global rank {global_rank or 'N/A'} of {total_participants:,} → {clout_rank:.1f}/30 pts. "
        f"Hard solved {hard_solved} → {clout_hard:.1f}/20 pts."
    )

    # ── CONSISTENCY ──
    # Based on 12-month contest participation array
    monthly = extra.get("monthly_practice_counts") or [0] * 12
    if sum(monthly) == 0:
        monthly = extra.get("monthly_contest_counts") or [0] * 12
    mu = sum(monthly) / max(len(monthly), 1)
    sigma = compute_consistency_sigma(monthly)
    
    # High mean + low CV = good consistency
    active_months = sum(1 for m in monthly if m > 0)
    cons_base = clamp(active_months / 12 * 60)  # Up to 60 pts for active months
    
    # Penalise high standard deviation (irregular pattern)
    cv = sigma / (mu + 1e-9)
    cons_penalty = clamp(cv * 20, 0, 20)
    cons_val = clamp(cons_base - cons_penalty + mu * 5)

    cons_reason = (
        f"Active months: {active_months}/12 → {cons_base:.1f}/60 base pts. "
        f"Monthly mean {mu:.2f}, std {sigma:.2f}, CV {cv:.2f} → penalty {cons_penalty:.1f}. "
        f"Final consistency: {cons_val:.1f}/100."
    )

    # ── VELOCITY ──
    # Acceptance rate + solved volume
    total_accepted = extra.get("total_accepted", 0) or 0
    total_attempted = extra.get("total_attempted", 0) or 0
    solved = profile.solved_count or 0

    if total_attempted > 0:
        accept_rate = total_accepted / total_attempted
    elif solved > 0:
        accept_rate = 0.75  # assume decent rate
    else:
        accept_rate = 0.0

    vel_acceptance = clamp(accept_rate * 60)
    vel_volume = clamp(solved / 400 * 40)  # 400 solved = full 40 pts
    vel_val = clamp(vel_acceptance + vel_volume)

    vel_reason = (
        f"Acceptance rate {accept_rate:.1%} → {vel_acceptance:.1f}/60 pts. "
        f"Total solved {solved} → {vel_volume:.1f}/40 pts."
    )

    platform_score = clamp(clout_val * 0.4 + cons_val * 0.3 + vel_val * 0.3)

    return PlatformScore(
        platform="LeetCode",
        weight=PLATFORM_WEIGHTS["LeetCode"],
        clout=SubScoreExplanation(raw_value=contest_rating, score=round(clout_val, 2), formula="0.4 × (ContestRating/3000×50 + RankPct×30 + HardSolved/50×20)", reasoning=clout_reason),
        consistency=SubScoreExplanation(raw_value=mu, score=round(cons_val, 2), formula="ActiveMonths/12×60 − CV×20 + μ×5", reasoning=cons_reason),
        velocity=SubScoreExplanation(raw_value=accept_rate, score=round(vel_val, 2), formula="AcceptRate×60 + Solved/400×40", reasoning=vel_reason),
        platform_score=round(platform_score, 2),
        reasoning=f"LeetCode platform score: {platform_score:.1f}/100 (Clout {clout_val:.1f}, Consistency {cons_val:.1f}, Velocity {vel_val:.1f})"
    )


def score_codeforces(profile: ScraperOutput) -> PlatformScore:
    extra = profile.extra or {}
    
    # ── CLOUT ──
    # Elo rating is the primary signal. CF ratings go 0..3500+
    max_rating = extra.get("max_rating") or profile.rating or 0
    # Clout = min(max_rating / 3500, 1) * 100
    clout_val = clamp(max_rating / 3500 * 100)
    clout_reason = (
        f"Max rating {max_rating} on Codeforces (scale 0–3500). "
        f"Clout = {max_rating}/3500 × 100 = {clout_val:.1f}/100."
    )

    # ── CONSISTENCY ──
    # C_90 = contests in last 90 days. C_target = 6 (highly active)
    c_90 = extra.get("contests_last_90", 0) or 0
    c_target = 6
    if c_90 > 0:
        cons_val = clamp(c_90 / c_target * 100)
        cons_reason = (
            f"Contests attended in last 90 days: {c_90} (target = {c_target} for full score). "
            f"Consistency = {c_90}/{c_target} × 100 = {cons_val:.1f}/100."
        )
    else:
        # Fallback to practice active days in last 90 days (15 active days = 100% consistency)
        active_days_90 = extra.get("active_days_90", 0) or 0
        cons_val = clamp(active_days_90 / 15 * 100)
        cons_reason = (
            f"No contest attended in last 90 days. Active practice days: {active_days_90}/15. "
            f"Consistency = {cons_val:.1f}/100."
        )

    # ── VELOCITY ──
    # Penalise wrong answers during contests
    wrong = extra.get("wrong_during_contest", 0) or 0
    total_contests = extra.get("total_contest_count", 1) or 1
    wrong_per_contest = wrong / max(total_contests, 1)
    # Fewer wrong submissions per contest = higher velocity
    vel_val = clamp(100 - wrong_per_contest * 5)  # Each wrong attempt/contest subtracts 5 pts
    vel_reason = (
        f"Wrong submissions during contests: {wrong} across {total_contests} contests = "
        f"{wrong_per_contest:.2f} wrong/contest. "
        f"Velocity = 100 − {wrong_per_contest:.2f}×5 = {vel_val:.1f}/100."
    )

    platform_score = clamp(clout_val * 0.4 + cons_val * 0.3 + vel_val * 0.3)

    return PlatformScore(
        platform="Codeforces",
        weight=PLATFORM_WEIGHTS["Codeforces"],
        clout=SubScoreExplanation(raw_value=max_rating, score=round(clout_val, 2), formula="MaxRating / 3500 × 100", reasoning=clout_reason),
        consistency=SubScoreExplanation(raw_value=c_90, score=round(cons_val, 2), formula="C_90 / C_target(6) × 100", reasoning=cons_reason),
        velocity=SubScoreExplanation(raw_value=wrong_per_contest, score=round(vel_val, 2), formula="100 − (WrongDuringContests / TotalContests) × 5", reasoning=vel_reason),
        platform_score=round(platform_score, 2),
        reasoning=f"Codeforces platform score: {platform_score:.1f}/100 (Clout {clout_val:.1f}, Consistency {cons_val:.1f}, Velocity {vel_val:.1f})"
    )


def score_codechef(profile: ScraperOutput) -> PlatformScore:
    extra = profile.extra or {}
    
    # ── CLOUT ──
    # Stars (1–7) + global rank ceiling
    stars = extra.get("stars") or 0
    rating = profile.rating or 0
    global_rank = extra.get("global_rank") or None
    
    # Stars give a tier bonus (1 star = 14%, 7 stars = 100%)
    stars_contribution = clamp(stars / 7 * 60)
    # Rating contribution  
    rating_contribution = clamp(rating / 3000 * 40) if rating > 0 else 0
    clout_val = clamp(stars_contribution + rating_contribution)
    clout_reason = (
        f"Stars: {stars}/7 → {stars_contribution:.1f}/60 pts. "
        f"Rating: {rating} → {rating_contribution:.1f}/40 pts. "
        f"Total clout: {clout_val:.1f}/100."
    )

    # ── CONSISTENCY ──
    # Problem solving presence. We use solved count as proxy.
    solved = profile.solved_count or 0
    cons_val = clamp(solved / 200 * 100)  # 200 solved = full score
    cons_reason = f"Total problems solved: {solved} (200 = full score). Consistency: {cons_val:.1f}/100."

    # ── VELOCITY ──
    # Full vs partial completion ratio
    fully = extra.get("fully_solved") or solved
    partially = extra.get("partially_solved") or 0
    total_attempted = (fully or 0) + (partially or 0)
    if total_attempted > 0:
        full_ratio = (fully or 0) / total_attempted
    else:
        full_ratio = 1.0 if solved > 0 else 0.0
    vel_val = clamp(full_ratio * 100)
    vel_reason = (
        f"Fully solved: {fully}, Partially solved: {partially}. "
        f"Full completion ratio = {full_ratio:.1%}. Velocity: {vel_val:.1f}/100."
    )

    platform_score = clamp(clout_val * 0.4 + cons_val * 0.3 + vel_val * 0.3)

    return PlatformScore(
        platform="CodeChef",
        weight=PLATFORM_WEIGHTS["CodeChef"],
        clout=SubScoreExplanation(raw_value=rating, score=round(clout_val, 2), formula="Stars/7×60 + Rating/3000×40", reasoning=clout_reason),
        consistency=SubScoreExplanation(raw_value=solved, score=round(cons_val, 2), formula="Solved / 200 × 100", reasoning=cons_reason),
        velocity=SubScoreExplanation(raw_value=full_ratio, score=round(vel_val, 2), formula="FullySolved / (Fully + Partially) × 100", reasoning=vel_reason),
        platform_score=round(platform_score, 2),
        reasoning=f"CodeChef platform score: {platform_score:.1f}/100 (Clout {clout_val:.1f}, Consistency {cons_val:.1f}, Velocity {vel_val:.1f})"
    )


def score_hackerrank(profile: ScraperOutput) -> PlatformScore:
    extra = profile.extra or {}
    
    # ── CLOUT ──
    # Badge star counts — sum and normalise against a realistic cap of 12 stars
    badge_stars = extra.get("badge_stars") or []
    total_badge_stars = sum(badge_stars)
    clout_val = clamp(total_badge_stars / 12 * 100)
    clout_reason = (
        f"Total badge stars across {len(badge_stars)} badges: {total_badge_stars} "
        f"(max ~12). Clout: {clout_val:.1f}/100."
    )

    # ── CONSISTENCY ──
    # Total score / account lifespan (capped at 365 days to avoid penalising older accounts)
    total_score = extra.get("total_score") or 0
    created_at = extra.get("created_at")
    days_active = 365
    if created_at:
        try:
            created = datetime.fromisoformat(str(created_at).replace("Z", "+00:00"))
            days_active = max(1, (datetime.now(timezone.utc) - created).days)
        except Exception:
            pass
    
    days_active_cap = min(365, days_active)
    score_per_day = total_score / days_active_cap if days_active_cap > 0 else 0
    cons_val = clamp(score_per_day * 10)  # 10 points/day = full score
    cons_reason = (
        f"Total HackerRank score: {total_score}. Account age: {days_active} days (capped at 365 for practice consistency). "
        f"Score/day = {score_per_day:.2f}. Consistency: {cons_val:.1f}/100."
    )

    # ── VELOCITY ──
    # Sum solved count from badges, proxy perfect challenges as 75% of solved challenges
    solved_count = profile.solved_count or 0
    perfect = max(1, int(solved_count * 0.75)) if solved_count > 0 else 0
    vel_val = clamp(perfect / 20 * 100)  # 20 perfect = full score
    vel_reason = f"Perfect challenges: {perfect} (derived as 75% of {solved_count} solved). Velocity: {vel_val:.1f}/100."

    platform_score = clamp(clout_val * 0.4 + cons_val * 0.3 + vel_val * 0.3)

    return PlatformScore(
        platform="HackerRank",
        weight=PLATFORM_WEIGHTS["HackerRank"],
        clout=SubScoreExplanation(raw_value=total_badge_stars, score=round(clout_val, 2), formula="TotalBadgeStars / 12 × 100", reasoning=clout_reason),
        consistency=SubScoreExplanation(raw_value=score_per_day, score=round(cons_val, 2), formula="TotalScore / min(365, DaysActive) × 10", reasoning=cons_reason),
        velocity=SubScoreExplanation(raw_value=perfect, score=round(vel_val, 2), formula="PerfectChallenges (75% of solved) / 20 × 100", reasoning=vel_reason),
        platform_score=round(platform_score, 2),
        reasoning=f"HackerRank platform score: {platform_score:.1f}/100 (Clout {clout_val:.1f}, Consistency {cons_val:.1f}, Velocity {vel_val:.1f})"
    )


def score_geeksforgeeks(profile: ScraperOutput) -> PlatformScore:
    extra = profile.extra or {}

    # ── CLOUT ──
    # Normalise coding score on GFG (cumulative operational metric)
    coding_score = extra.get("coding_score") or profile.rating or 0
    solved = profile.solved_count or 0
    # GFG scores can go into thousands; normalise against a soft cap of 1000
    clout_score_part = clamp(coding_score / 1000 * 70)
    clout_solved_part = clamp(solved / 200 * 30)
    clout_val = clamp(clout_score_part + clout_solved_part)
    clout_reason = (
        f"Coding score: {coding_score} (cap 1000) → {clout_score_part:.1f}/70 pts. "
        f"Problems solved: {solved} (cap 200) → {clout_solved_part:.1f}/30 pts. "
        f"Total clout: {clout_val:.1f}/100."
    )

    # ── CONSISTENCY ──
    # POTD streak adherence
    current_streak = extra.get("current_potd_streak") or 0
    longest_streak = extra.get("longest_potd_streak") or 0
    days_active = extra.get("days_active") or 1
    
    # Normalise current streak (60 days = full) + longest streak bonus
    streak_score = clamp(current_streak / 60 * 70)
    longest_bonus = clamp(longest_streak / 100 * 30)
    cons_val = clamp(streak_score + longest_bonus)
    cons_reason = (
        f"Current POTD streak: {current_streak} (60 = full) → {streak_score:.1f}/70 pts. "
        f"Longest streak: {longest_streak} (100 = full) → {longest_bonus:.1f}/30 pts. "
        f"Total consistency: {cons_val:.1f}/100."
    )

    # ── VELOCITY ──
    # Submission accuracy
    accuracy = extra.get("accuracy")
    if accuracy is None:
        accuracy = 50.0  # neutral default
    vel_val = clamp(accuracy)
    vel_reason = f"GFG submission accuracy: {accuracy:.1f}%. Velocity score: {vel_val:.1f}/100."

    platform_score = clamp(clout_val * 0.4 + cons_val * 0.3 + vel_val * 0.3)

    return PlatformScore(
        platform="GeeksForGeeks",
        weight=PLATFORM_WEIGHTS["GeeksForGeeks"],
        clout=SubScoreExplanation(raw_value=coding_score, score=round(clout_val, 2), formula="CodingScore/1000×70 + Solved/200×30", reasoning=clout_reason),
        consistency=SubScoreExplanation(raw_value=current_streak, score=round(cons_val, 2), formula="CurrentStreak/60×70 + LongestStreak/100×30", reasoning=cons_reason),
        velocity=SubScoreExplanation(raw_value=accuracy, score=round(vel_val, 2), formula="AccuracyPct (from correct POTD / days_active × 100)", reasoning=vel_reason),
        platform_score=round(platform_score, 2),
        reasoning=f"GeeksForGeeks platform score: {platform_score:.1f}/100 (Clout {clout_val:.1f}, Consistency {cons_val:.1f}, Velocity {vel_val:.1f})"
    )


SCORERS = {
    "LeetCode": score_leetcode,
    "Codeforces": score_codeforces,
    "CodeChef": score_codechef,
    "HackerRank": score_hackerrank,
}

def compute_cp_scoring(profiles: List[ScraperOutput]) -> ScoringResult:
    """
    Dynamic Platform Behavior Persona weight allocation:
    1. Compute raw platform scores for available platforms.
    2. Re-normalise weights so they sum to 1.0 across present platforms.
    3. Handle edge cases: single-platform, seasonal hopper, etc.
    """
    platform_scores: List[PlatformScore] = []
    
    for profile in profiles:
        scorer = SCORERS.get(profile.platform)
        if scorer:
            try:
                ps = scorer(profile)
                platform_scores.append(ps)
            except Exception as e:
                # Don't crash if scoring fails for a platform
                pass

    if not platform_scores:
        return ScoringResult(
            platform_scores=[],
            final_score=0.0,
            score_tier="Unranked",
            aggregation_method="No scorable platforms found",
            overall_reasoning="No platforms with scorable data were provided."
        )

    # Dynamic weight re-normalisation
    total_base_weight = sum(PLATFORM_WEIGHTS.get(ps.platform, 0.1) for ps in platform_scores)
    
    if total_base_weight == 0:
        total_base_weight = 1.0

    normalised_scores = []
    for ps in platform_scores:
        base_w = PLATFORM_WEIGHTS.get(ps.platform, 0.1)
        normalised_w = base_w / total_base_weight
        # Update the weight field to reflect normalised value
        normalised_scores.append((ps, normalised_w))

    # Edge Case 1: Single-Platform God — no penalties, full weight to that platform
    if len(platform_scores) == 1:
        ps, _ = normalised_scores[0]
        final = ps.platform_score
        method = "Single-Platform God: Full weight to sole active platform. No inactivity penalty."
        reasoning = (
            f"Only {ps.platform} data available. Full score = {final:.1f}/100. "
            f"This student excels on a single platform — score is taken at face value with no cross-platform penalties."
        )
    else:
        # Edge Case 2: Multi-platform — seasonal hopper check
        # A seasonal hopper has high variance across platforms; we use a harmony-mean approach
        scores_list = [ps.platform_score for ps, _ in normalised_scores]
        mu = sum(scores_list) / len(scores_list)
        variance = sum((s - mu)**2 for s in scores_list) / len(scores_list)
        std_dev = math.sqrt(variance)
        
        # Weighted average
        weighted_avg = sum(ps.platform_score * w for ps, w in normalised_scores)
        
        # If high variance (std > 25), apply a soft consistency bonus/penalty
        # based on how consistently they perform across all platforms
        cv = std_dev / (mu + 1e-9)
        if cv > 0.5 and len(platform_scores) > 1:
            # Seasonal hopper — average is reliable; apply small CV penalty
            cv_penalty = min(cv * 5, 10)  # max 10 pts penalty
            final = clamp(weighted_avg - cv_penalty)
            method = (
                f"Seasonal Platform Hopper: High CV={cv:.2f} detected across {len(platform_scores)} platforms. "
                f"Applied {cv_penalty:.1f} pt cross-platform consistency penalty."
            )
        else:
            final = weighted_avg
            method = f"Weighted Average across {len(platform_scores)} platforms (normalised weights summing to 1.0)."

        reasoning = (
            f"Platform scores: " +
            ", ".join(f"{ps.platform}={ps.platform_score:.1f}(w={w:.2f})" for ps, w in normalised_scores) +
            f". Weighted avg = {weighted_avg:.1f}. Final score = {final:.1f}/100."
        )

    # Update platform weights to normalised values
    for i, (ps, nw) in enumerate(normalised_scores):
        platform_scores[i] = ps.copy(update={"weight": round(nw, 3)})

    # Score tier
    def get_tier(score: float) -> str:
        if score >= 90: return "🏆 Elite Competitive Programmer"
        if score >= 75: return "🥇 Advanced Coder"
        if score >= 60: return "🥈 Proficient Developer"
        if score >= 45: return "🥉 Intermediate Practitioner"
        if score >= 30: return "🌱 Developing Coder"
        return "🔰 Beginner"

    return ScoringResult(
        platform_scores=platform_scores,
        final_score=round(final, 2),
        score_tier=get_tier(final),
        aggregation_method=method,
        overall_reasoning=reasoning
    )


# ─── Legacy Evaluation + Scores (for backward compat) ────────────────────────

async def evaluate_code_metrics(profiles: List[ScraperOutput]) -> CodeEvaluationOutput:
    leetcode_percentile: Optional[float] = None
    code_red_flags = []
    profile_count = len(profiles)

    for profile in profiles:
        if profile.platform == "LeetCode" and profile.rank is not None:
            percentile = max(0.0, min(100.0, 100.0 - (profile.rank or 0) / 100000.0 * 100.0))
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


def compute_scores_and_analysis(
    profiles: List[ScraperOutput],
    evaluation: CodeEvaluationOutput,
    cp_scoring: Optional[ScoringResult] = None
) -> tuple:
    total_solved = sum(p.solved_count or 0 for p in profiles)
    easy = medium = hard = 0
    for p in profiles:
        if p.problems_by_difficulty:
            easy += p.problems_by_difficulty.get("Easy", 0)
            medium += p.problems_by_difficulty.get("Medium", 0)
            hard += p.problems_by_difficulty.get("Hard", 0)

    ratings = [p.rating for p in profiles if p.rating is not None]
    max_rating = max(ratings) if ratings else 0
    contest_ratings = [p.contest_rating for p in profiles if p.contest_rating is not None]
    max_contest = max(contest_ratings) if contest_ratings else 0

    has_cp = any(p.platform in ("Codeforces", "CodeChef") and p.rating is not None for p in profiles)
    platform_count = len(profiles)

    # Use CP scoring engine's final score as the base if available
    if cp_scoring and cp_scoring.final_score > 0:
        overall = int(clamp(cp_scoring.final_score))
    else:
        dsa_score = min(100, int(
            min(total_solved / 5, 30) +
            min(easy / 3, 10) +
            min(medium / 2, 25) +
            min(hard * 5, 35)
        ))
        cp_score = 0
        if has_cp:
            cp_score = min(100, int(
                min(max_rating / 35, 50) +
                min(max_contest / 35, 30) +
                min(total_solved / 10, 20)
            ))
        consistency = min(100, platform_count * 20 + min(total_solved / 2, 40))
        interview_score = min(100, int(
            min(total_solved / 3, 25) +
            min(medium * 2, 30) +
            min(hard * 5, 30) +
            (15 if total_solved >= 100 else min(total_solved / 7, 15))
        ))
        overall = int(dsa_score * 0.35 + cp_score * 0.25 + consistency * 0.15 + interview_score * 0.25)

    dsa_score = min(100, int(
        min(total_solved / 5, 30) + min(easy / 3, 10) + min(medium / 2, 25) + min(hard * 5, 35)
    ))
    cp_score = 0
    if has_cp:
        cp_score = min(100, int(min(max_rating / 35, 50) + min(max_contest / 35, 30) + min(total_solved / 10, 20)))
    consistency = min(100, platform_count * 20 + min(total_solved / 2, 40))
    interview_score = min(100, int(
        min(total_solved / 3, 25) + min(medium * 2, 30) + min(hard * 5, 30) +
        (15 if total_solved >= 100 else min(total_solved / 7, 15))
    ))

    def label(score):
        if score >= 80: return "excellent"
        if score >= 60: return "strong"
        if score >= 40: return "moderate"
        if score >= 20: return "beginner"
        return "none"

    def readiness(score):
        if score >= 75: return "ready"
        if score >= 50: return "almost_ready"
        if score >= 25: return "developing"
        return "not_ready"

    scores = ScoreBreakdown(
        overall_score=int(clamp(overall)),
        dsa_strength=label(dsa_score),
        competitive_programming=label(cp_score),
        open_source="none",
        interview_readiness=readiness(interview_score),
        faang_readiness=readiness(min(interview_score, dsa_score, cp_score)),
    )

    radar = SkillsRadar(
        dsa=dsa_score,
        cp=cp_score,
        open_source=0,
        consistency=int(consistency),
        interview=interview_score,
    )

    # AI Analysis
    strengths, weaknesses, recommended_topics, next_steps = [], [], [], []

    if total_solved > 0:
        strengths.append("Taking initiative to build coding profiles")
    if total_solved >= 100:
        strengths.append(f"Strong problem-solving volume with {total_solved} problems solved")
    if hard >= 10:
        strengths.append(f"Good hard problem count ({hard}) shows algorithmic depth")
    if medium >= 30:
        strengths.append(f"Solid medium problem practice ({medium} solved)")
    if max_rating >= 1400:
        strengths.append(f"Competitive rating of {max_rating} shows contest experience")
    if platform_count >= 3:
        strengths.append(f"Active on {platform_count} platforms showing broad engagement")

    # Incorporate CP scoring insights
    if cp_scoring:
        for ps in cp_scoring.platform_scores:
            if ps.platform_score >= 70:
                strengths.append(f"Strong {ps.platform} performance (score: {ps.platform_score:.0f}/100)")

    if not strengths:
        strengths.append("Getting started on the coding journey")

    if hard < 10:
        weaknesses.append(f"Only {hard} hard problems solved — need more practice with complex algorithms")
    if not has_cp:
        weaknesses.append("No competitive programming presence — contests build speed and accuracy")
    if total_solved < 50:
        weaknesses.append(f"Low total solved count ({total_solved}) — aim for 150+ for strong foundations")
    if medium < 20:
        weaknesses.append(f"Only {medium} medium problems — these are crucial for interview prep")

    # Add CP scoring weaknesses
    if cp_scoring:
        for ps in cp_scoring.platform_scores:
            if ps.consistency.score < 40:
                weaknesses.append(f"Low consistency on {ps.platform} (score: {ps.consistency.score:.0f}/100) — try to practice regularly")
            if ps.velocity.score < 40:
                weaknesses.append(f"Low velocity/accuracy on {ps.platform} (score: {ps.velocity.score:.0f}/100) — focus on quality over quantity")

    weaknesses.append("Limited open source presence — projects demonstrate real-world skills")

    if hard < 10:
        recommended_topics += ["Dynamic Programming", "Graph Algorithms"]
    if medium < 30:
        recommended_topics += ["Two Pointers and Sliding Window", "Binary Search variations"]
    if total_solved < 100:
        recommended_topics.append("Arrays and Strings fundamentals")
    recommended_topics.append("System Design")
    if not has_cp:
        recommended_topics.append("Contest problem solving strategies")

    if hard < 20:
        next_steps.append(f"Increase hard problem count from {hard} to {max(hard + 18, 20)}")
    if total_solved < 150:
        next_steps.append(f"Push total solved count from {total_solved} toward {total_solved + 50}")
    if not has_cp:
        next_steps.append("Start participating in Codeforces or CodeChef contests weekly")
    elif max_rating < 1400:
        next_steps.append(f"Push contest rating from {max_rating} toward 1400+")
    next_steps.append("Start 2-3 meaningful personal projects on GitHub")

    diff_parts = []
    if easy: diff_parts.append(f"{easy}E")
    if medium: diff_parts.append(f"{medium}M")
    if hard: diff_parts.append(f"{hard}H")
    diff_str = "/".join(diff_parts)
    diff_detail = f" ({diff_str})" if diff_str else ""

    platform_names = [p.platform for p in profiles]
    feedback = f"Your profile shows {total_solved} problems solved{diff_detail} across {', '.join(platform_names)}. "

    if cp_scoring:
        feedback += f"Your CP-Agent composite score is {cp_scoring.final_score:.1f}/100 — tier: {cp_scoring.score_tier}. "

    if total_solved < 50:
        feedback += "Focus on building fundamentals through daily practice. Start with easy problems and gradually move to medium."
    elif total_solved < 150:
        feedback += "Focus on building fundamentals through regular practice. Prioritize solving more hard problems to build confidence with complex algorithms."
    else:
        feedback += "You have solid volume. Focus on quality over quantity — target hard problems and participate in contests to improve speed."

    analysis = AIAnalysis(
        strengths=strengths[:6],
        weaknesses=weaknesses[:5],
        recommended_topics=recommended_topics[:6],
        next_steps=next_steps[:5],
        personalized_feedback=feedback,
    )

    return scores, radar, analysis

# ─── FastAPI Endpoints ────────────────────────────────────────────────────────

def clean_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    val = str(url).strip()
    if val.lower() in ("", "nan", "—", "n/a", "null", "none", "-", "—"):
        return None
    # Ensure it looks like a valid URL or path
    if not ("http" in val or "." in val):
        return None
    return val

@app.post("/analyze", response_model=ProfileResponse)
async def analyze_profiles(request: Request):
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="Request body must be valid JSON")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=422, detail="Request JSON must be an object")

    query = ProfileQuery(
        student_name=payload.get("student_name"),
        leetcode=clean_url(payload.get("leetcode")),
        codeforces=clean_url(payload.get("codeforces")),
        codechef=clean_url(payload.get("codechef")),
        hackerrank=clean_url(payload.get("hackerrank")),
    )

    profiles: List[ScraperOutput] = []

    if query.leetcode:
        try:
            profiles.append(await scrape_leetcode(str(query.leetcode)))
        except Exception as e:
            print(f"Warning: failed to scrape LeetCode profile {query.leetcode}: {e}")
            
    if query.codeforces:
        try:
            profiles.append(await scrape_codeforces(str(query.codeforces)))
        except Exception as e:
            print(f"Warning: failed to scrape Codeforces profile {query.codeforces}: {e}")
            
    if query.codechef:
        try:
            profiles.append(await scrape_codechef(str(query.codechef)))
        except Exception as e:
            print(f"Warning: failed to scrape CodeChef profile {query.codechef}: {e}")
            
    if query.hackerrank:
        try:
            profiles.append(await scrape_hackerrank(str(query.hackerrank)))
        except Exception as e:
            print(f"Warning: failed to scrape HackerRank profile {query.hackerrank}: {e}")

    if not profiles:
        raise HTTPException(status_code=400, detail="Failed to scrape any valid profile. Please verify your profile URLs.")

    # Run CP-Agent scoring engine
    cp_scoring = compute_cp_scoring(profiles)

    evaluation = await evaluate_code_metrics(profiles)
    scores, radar, analysis = compute_scores_and_analysis(profiles, evaluation, cp_scoring)

    response_data = ProfileResponse(
        profiles=profiles,
        evaluation=evaluation,
        scores=scores,
        radar=radar,
        analysis=analysis,
        cp_scoring=cp_scoring,
    )

    if query.student_name:
        import datetime
        records = load_records()
        records[query.student_name] = {
            "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
            "data": response_data.dict()
        }
        with open(RECORDS_FILE, "w") as f:
            json.dump(records, f)

    return response_data

RECORDS_FILE = "records.json"

def load_records() -> dict:
    if os.path.exists(RECORDS_FILE):
        try:
            with open(RECORDS_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

@app.get("/records")
async def get_records():
    records = load_records()
    items = []
    for name, record in records.items():
        data = record.get("data", {})
        scores = data.get("scores", {})
        eval_data = data.get("evaluation", {})
        cf_score = "—"
        for p in data.get("profiles", []):
            if p.get("platform") == "Codeforces":
                cf_score = f"{p.get('solved_count', 0)} solved"
                if p.get("rating"):
                    cf_score += f" (Rating: {p.get('rating')})"
                break
        items.append({
            "name": name,
            "timestamp": record.get("timestamp", ""),
            "overall_score": scores.get("overall_score"),
            "leetcode_percentile": eval_data.get("leetcode_percentile"),
            "codeforces": cf_score,
            "dsa_strength": scores.get("dsa_strength", "").title().replace("_", " "),
            "cp_level": scores.get("cp_level", "").title().replace("_", " ")
        })
    items.sort(key=lambda x: x["timestamp"], reverse=True)
    return {"records": items}

@app.get("/records/{student_name}", response_model=ProfileResponse)
async def get_record(student_name: str):
    records = load_records()
    if student_name not in records:
        raise HTTPException(status_code=404, detail="Student not found")
    return records[student_name]["data"]

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
