#!/usr/bin/env python3
"""
Fetches YOUR personal GitHub contribution statistics across all repositories.
Counts lines YOU added (not repo totals).

Uses incremental caching: only re-scans commits newer than the last seen SHA.

Environment variables:
  METRICS_TOKEN / GITHUB_TOKEN  - GitHub PAT (required)
  GITHUB_USER                   - username (default: icetrahan)
  MAX_REPOS                     - cap repos scanned (default: 500)
  MAX_COMMITS_PER_REPO          - cap commits inspected per repo (default: 500)
  REQUEST_TIMEOUT               - per-request timeout seconds (default: 25)
  SKIP_FORKS                    - "1" to skip forks (default: 1)
  SKIP_ARCHIVED                 - "1" to skip archived (default: 1)
  MAX_RATELIMIT_WAIT            - max seconds to wait for rate limit reset (default: 300)
  CACHE_FILE                    - path to cache file (default: stats.json)
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from typing import Any

import requests

sys.stdout.reconfigure(line_buffering=True)

GITHUB_TOKEN = os.environ.get("METRICS_TOKEN") or os.environ.get("GITHUB_TOKEN")
USERNAME = os.environ.get("GITHUB_USER", "icetrahan")
MAX_REPOS = int(os.environ.get("MAX_REPOS", "500"))
MAX_COMMITS_PER_REPO = int(os.environ.get("MAX_COMMITS_PER_REPO", "500"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "25"))
SKIP_FORKS = os.environ.get("SKIP_FORKS", "1") == "1"
SKIP_ARCHIVED = os.environ.get("SKIP_ARCHIVED", "1") == "1"
MAX_RATELIMIT_WAIT = int(os.environ.get("MAX_RATELIMIT_WAIT", "300"))

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
CACHE_FILE = os.environ.get("CACHE_FILE", os.path.join(REPO_ROOT, "stats.json"))
SVG_FILE = os.path.join(REPO_ROOT, "code-stats.svg")

API = "https://api.github.com"
START = time.monotonic()


def log(msg: str) -> None:
    print(f"[{time.monotonic() - START:7.2f}s] {msg}", flush=True)


def warn(msg: str) -> None:
    log(f"WARN: {msg}")


LANG_COLORS = {
    "Python": "#3572A5", "JavaScript": "#f1e05a", "TypeScript": "#3178c6",
    "Dart": "#00B4AB", "C#": "#178600", "Java": "#b07219", "HTML": "#e34c26",
    "CSS": "#563d7c", "SCSS": "#c6538c", "Shell": "#89e051", "Dockerfile": "#384d54",
    "SQL": "#e38c00", "C++": "#f34b7d", "C": "#555555", "Go": "#00ADD8",
    "Rust": "#dea584", "PHP": "#4F5D95", "Ruby": "#701516", "Swift": "#F05138",
    "Kotlin": "#A97BFF", "Vue": "#41b883", "Svelte": "#ff3e00", "Markdown": "#083fa1",
    "JSON": "#292929", "YAML": "#cb171e", "Lua": "#000080", "GDScript": "#355570",
}

EXT_TO_LANG = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".jsx": "JavaScript", ".dart": "Dart", ".cs": "C#", ".java": "Java",
    ".cpp": "C++", ".cc": "C++", ".cxx": "C++", ".hpp": "C++", ".h": "C++",
    ".c": "C", ".go": "Go", ".rs": "Rust", ".php": "PHP", ".rb": "Ruby",
    ".swift": "Swift", ".kt": "Kotlin", ".kts": "Kotlin", ".vue": "Vue",
    ".svelte": "Svelte", ".lua": "Lua", ".gd": "GDScript", ".sql": "SQL",
    ".sh": "Shell", ".bash": "Shell",
}


# ---------------------------------------------------------------------------
# Rate-limit-aware HTTP
# ---------------------------------------------------------------------------

SESSION = requests.Session()
if GITHUB_TOKEN:
    SESSION.headers.update(
        {
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"{USERNAME}-stats-generator",
        }
    )


class RateLimitExhausted(Exception):
    """Raised when we've decided to stop due to rate limiting."""


def _sleep_for_rate_limit(resp: requests.Response) -> bool:
    """Return True if we slept and should retry, False if we should give up."""
    remaining = resp.headers.get("X-RateLimit-Remaining")
    reset = int(resp.headers.get("X-RateLimit-Reset", "0") or 0)

    if remaining is not None and remaining.isdigit() and int(remaining) == 0:
        wait = max(5, reset - int(time.time())) if reset else 60
        if wait > MAX_RATELIMIT_WAIT:
            warn(f"rate limit reset in {wait}s exceeds MAX_RATELIMIT_WAIT={MAX_RATELIMIT_WAIT}s; stopping")
            raise RateLimitExhausted()
        warn(f"primary rate limit hit; sleeping {wait}s")
        time.sleep(wait)
        return True

    if resp.status_code in (403, 429):
        retry_after = resp.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            wait = int(retry_after)
        else:
            wait = 30
        if wait > MAX_RATELIMIT_WAIT:
            warn(f"secondary rate limit retry-after={wait}s exceeds budget; stopping")
            raise RateLimitExhausted()
        warn(f"secondary rate limit (status {resp.status_code}); sleeping {wait}s")
        time.sleep(wait)
        return True

    return False


def gh_get(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    max_retries: int = 3,
    quiet: bool = False,
) -> requests.Response | None:
    url = path if path.startswith("http") else f"{API}{path}"
    for attempt in range(1, max_retries + 1):
        try:
            if not quiet:
                short = url[len(API):] if url.startswith(API) else url
                log(f"GET {short}  params={params or {}}  (try {attempt}/{max_retries})")
            resp = SESSION.get(url, params=params, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            warn(f"network error: {exc!r}; retry in {2 ** attempt}s")
            time.sleep(2 ** attempt)
            continue

        if not quiet:
            log(f"  -> {resp.status_code}  rate-remaining={resp.headers.get('X-RateLimit-Remaining', '?')}")

        if resp.status_code in (200, 404, 409):
            return resp

        if _sleep_for_rate_limit(resp):
            continue

        if 500 <= resp.status_code < 600:
            warn(f"server error {resp.status_code}; retry in {2 ** attempt}s")
            time.sleep(2 ** attempt)
            continue

        warn(f"unexpected {resp.status_code}: {resp.text[:200]}")
        return resp

    warn(f"giving up on {url}")
    return None


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------


def load_cache() -> dict:
    """Load previous stats.json cache if present."""
    if not os.path.exists(CACHE_FILE):
        log("no cache file present (first run)")
        return {}
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        cache = data.get("_cache", {})
        log(f"loaded cache with {len(cache)} repo entries")
        return cache
    except (json.JSONDecodeError, OSError) as exc:
        warn(f"could not read cache: {exc!r}")
        return {}


def cache_entry_for(cache: dict, full_name: str) -> dict:
    """Normalize/return the cache entry for a repo."""
    entry = cache.get(full_name) or {}
    entry.setdefault("last_sha", None)
    entry.setdefault("total_commits", 0)
    entry.setdefault("total_lines", 0)
    entry.setdefault("lang_lines", {})
    return entry


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------


def fetch_all_repos() -> list[dict]:
    log("Fetching repositories (owner + collaborator + organization_member)")
    repos: list[dict] = []
    page = 1
    while True:
        resp = gh_get(
            "/user/repos",
            {
                "per_page": 100,
                "page": page,
                "affiliation": "owner,collaborator,organization_member",
                "sort": "updated",
            },
        )
        if resp is None or resp.status_code != 200:
            warn(f"repo fetch stopped on page {page}")
            break
        data = resp.json() or []
        if not data:
            break
        repos.extend(data)
        log(f"  page {page}: +{len(data)} repos (total {len(repos)})")
        if len(repos) >= MAX_REPOS:
            repos = repos[:MAX_REPOS]
            log(f"  MAX_REPOS={MAX_REPOS} reached")
            break
        page += 1
    return repos


def fetch_new_commit_shas(owner: str, repo: str, last_seen_sha: str | None) -> list[str]:
    """Fetch commit SHAs by USER newer than last_seen_sha (commits come newest-first)."""
    shas: list[str] = []
    page = 1
    while len(shas) < MAX_COMMITS_PER_REPO:
        resp = gh_get(
            f"/repos/{owner}/{repo}/commits",
            {"author": USERNAME, "per_page": 100, "page": page},
            quiet=True,
        )
        if resp is None:
            break
        if resp.status_code == 409:
            return []
        if resp.status_code != 200:
            break
        commits = resp.json() or []
        if not commits:
            break
        for c in commits:
            if last_seen_sha and c["sha"] == last_seen_sha:
                log(f"    reached cached SHA {last_seen_sha[:7]}, stopping")
                return shas
            shas.append(c["sha"])
            if len(shas) >= MAX_COMMITS_PER_REPO:
                break
        if len(commits) < 100:
            break
        page += 1
    return shas


def fetch_commit_diffs(owner: str, repo: str, shas: list[str]) -> tuple[dict[str, int], int, int]:
    """Return (lang_additions, total_additions, commits_scanned)."""
    lang_additions: dict[str, int] = defaultdict(int)
    total_additions = 0
    scanned = 0

    for i, sha in enumerate(shas, start=1):
        resp = gh_get(f"/repos/{owner}/{repo}/commits/{sha}", quiet=True)
        if resp is None or resp.status_code != 200:
            continue
        data = resp.json()
        for f in data.get("files", []) or []:
            filename = f.get("filename", "")
            additions = f.get("additions", 0) or 0
            if additions <= 0:
                continue
            ext = os.path.splitext(filename)[1].lower()
            lang = EXT_TO_LANG.get(ext)
            if lang:
                lang_additions[lang] += additions
                total_additions += additions
        scanned += 1

        if i % 25 == 0:
            log(f"    ...{i}/{len(shas)} diffs scanned, {total_additions} new lines so far")

    return lang_additions, total_additions, scanned


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def format_number(num: int) -> str:
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M"
    if num >= 1_000:
        return f"{num / 1_000:.1f}k"
    return str(num)


def generate_svg(lang_stats: dict, total_lines: int, total_commits: int, total_repos: int) -> str:
    sorted_langs = sorted(lang_stats.items(), key=lambda x: x[1], reverse=True)[:8]
    if not sorted_langs:
        sorted_langs = [("No data yet", 0)]

    total = sum(l[1] for l in sorted_langs) or 1
    lang_data = []
    for lang, lines in sorted_langs:
        pct = (lines / total * 100) if total > 0 else 0
        lang_data.append(
            {"name": lang, "lines": lines, "pct": pct, "color": LANG_COLORS.get(lang, "#858585")}
        )

    width = 480
    bar_height = 40
    padding = 20
    header_height = 80
    height = header_height + len(lang_data) * bar_height + padding * 2 + 10

    svg = f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <style>
      .bg {{ fill: #0d1117; }}
      .title {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; font-size: 16px; font-weight: 600; fill: #58a6ff; }}
      .subtitle {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; font-size: 12px; fill: #8b949e; }}
      .lang-name {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; font-size: 13px; font-weight: 600; fill: #c9d1d9; }}
      .lang-stats {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; font-size: 12px; fill: #8b949e; }}
      .bar-bg {{ fill: #21262d; }}
    </style>
  </defs>
  <rect class="bg" width="{width}" height="{height}" rx="6"/>
  <rect x="0.5" y="0.5" width="{width-1}" height="{height-1}" rx="6" fill="none" stroke="#30363d" stroke-width="1"/>
  <text x="{padding}" y="32" class="title">📊 My Code Contributions</text>
  <text x="{padding}" y="52" class="subtitle">{format_number(total_lines)} lines added • {format_number(total_commits)} commits • {total_repos} repos analyzed</text>
'''

    y_offset = header_height + 5
    bar_width = width - padding * 2 - 140
    for lang in lang_data:
        fill_width = max((lang["pct"] / 100) * bar_width, 2)
        svg += f'''
  <g transform="translate({padding}, {y_offset})">
    <circle cx="6" cy="8" r="6" fill="{lang["color"]}"/>
    <text x="18" y="12" class="lang-name">{lang["name"]}</text>
    <rect x="0" y="20" width="{bar_width}" height="10" class="bar-bg" rx="5"/>
    <rect x="0" y="20" width="{fill_width}" height="10" fill="{lang["color"]}" rx="5"/>
    <text x="{bar_width + 15}" y="14" class="lang-stats">{format_number(lang["lines"])} lines</text>
    <text x="{bar_width + 15}" y="30" class="lang-stats" style="fill: #58a6ff;">{lang["pct"]:.1f}%</text>
  </g>
'''
        y_offset += bar_height
    svg += '</svg>'
    return svg


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def build_output(cache: dict) -> dict:
    """Aggregate all cached per-repo data into the final stats.json structure."""
    lang_stats: dict[str, int] = defaultdict(int)
    total_lines = 0
    total_commits = 0
    repos_with_contributions = 0

    for entry in cache.values():
        if entry.get("total_commits", 0) <= 0:
            continue
        repos_with_contributions += 1
        total_commits += entry["total_commits"]
        total_lines += entry["total_lines"]
        for lang, lines in entry.get("lang_lines", {}).items():
            lang_stats[lang] += lines

    sorted_langs = sorted(lang_stats.items(), key=lambda x: x[1], reverse=True)[:10]
    total = sum(l[1] for l in sorted_langs) or 1

    languages = [
        {
            "name": lang,
            "lines_added": lines,
            "percentage": round((lines / total * 100) if total else 0, 1),
            "color": LANG_COLORS.get(lang, "#858585"),
        }
        for lang, lines in sorted_langs
    ]

    return {
        "total_repos_analyzed": repos_with_contributions,
        "total_lines_added": total_lines,
        "total_commits": total_commits,
        "languages": languages,
        "_cache": cache,
    }


def write_outputs(cache: dict) -> None:
    output = build_output(cache)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)
    log(f"wrote {CACHE_FILE}")

    lang_stats = {lang["name"]: lang["lines_added"] for lang in output["languages"]}
    svg = generate_svg(
        lang_stats,
        output["total_lines_added"],
        output["total_commits"],
        output["total_repos_analyzed"],
    )
    with open(SVG_FILE, "w", encoding="utf-8") as f:
        f.write(svg)
    log(f"wrote {SVG_FILE}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    if not GITHUB_TOKEN:
        log("FATAL: no GitHub token (METRICS_TOKEN or GITHUB_TOKEN)")
        return 1

    log(f"user={USERNAME}  MAX_REPOS={MAX_REPOS}  MAX_COMMITS_PER_REPO={MAX_COMMITS_PER_REPO}")
    log(f"timeout={REQUEST_TIMEOUT}s  max_ratelimit_wait={MAX_RATELIMIT_WAIT}s")
    log(f"skip_forks={SKIP_FORKS}  skip_archived={SKIP_ARCHIVED}")

    rl = gh_get("/rate_limit")
    if rl is not None and rl.status_code == 200:
        core = rl.json().get("resources", {}).get("core", {})
        log(f"rate limit: {core.get('remaining')}/{core.get('limit')} remaining, "
            f"resets in {max(0, core.get('reset', 0) - int(time.time()))}s")

    cache = load_cache()
    repos = fetch_all_repos()
    log(f"Scanning {len(repos)} repositories (with incremental cache)")

    rate_limit_exhausted = False
    new_repos = 0
    cached_hits = 0
    delta_lines_total = 0

    for i, repo in enumerate(repos, start=1):
        if rate_limit_exhausted:
            log(f"[{i}/{len(repos)}] skipped (rate limit exhausted): {repo['full_name']}")
            continue

        owner = repo["owner"]["login"]
        name = repo["name"]
        full = f"{owner}/{name}"

        if SKIP_FORKS and repo.get("fork"):
            log(f"[{i}/{len(repos)}] skip fork: {full}")
            continue
        if SKIP_ARCHIVED and repo.get("archived"):
            log(f"[{i}/{len(repos)}] skip archived: {full}")
            continue

        entry = cache_entry_for(cache, full)
        last_sha = entry["last_sha"]
        log(f"[{i}/{len(repos)}] {full}  (cached: {entry['total_commits']} commits, last_sha={last_sha[:7] if last_sha else 'none'})")

        try:
            new_shas = fetch_new_commit_shas(owner, name, last_sha)
        except RateLimitExhausted:
            rate_limit_exhausted = True
            warn("stopping scan early; will save progress")
            continue

        if not new_shas:
            log(f"    no new commits since last scan")
            cached_hits += 1
            continue

        log(f"    {len(new_shas)} new commits to scan")
        if last_sha is None:
            new_repos += 1

        try:
            new_langs, new_lines, new_count = fetch_commit_diffs(owner, name, new_shas)
        except RateLimitExhausted:
            rate_limit_exhausted = True
            warn("stopping scan early; will save progress")
            continue

        for lang, lines in new_langs.items():
            entry["lang_lines"][lang] = entry["lang_lines"].get(lang, 0) + lines
        entry["total_lines"] += new_lines
        entry["total_commits"] += new_count
        entry["last_sha"] = new_shas[0]
        cache[full] = entry
        delta_lines_total += new_lines
        log(f"    +{new_count} commits, +{format_number(new_lines)} lines")

        if i % 10 == 0:
            write_outputs(cache)
            log(f"    [checkpoint] progress saved")

    log("=" * 60)
    log(f"Completed. new_repos={new_repos}  cached_hits={cached_hits}  "
        f"new_lines_this_run={format_number(delta_lines_total)}")
    if rate_limit_exhausted:
        log("NOTE: scan stopped early due to rate limiting; next run will resume")

    write_outputs(cache)

    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        final = json.load(f)
    log(f"TOTALS: {format_number(final['total_lines_added'])} lines, "
        f"{format_number(final['total_commits'])} commits, "
        f"{final['total_repos_analyzed']} repos")
    log("Contribution breakdown:")
    for lang in final["languages"]:
        log(f"   {lang['name']}: {format_number(lang['lines_added'])} lines ({lang['percentage']}%)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
