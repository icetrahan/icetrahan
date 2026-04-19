#!/usr/bin/env python3
"""
Fetches YOUR personal GitHub contribution statistics across all repositories.
Counts lines YOU added (not repo totals).

Environment variables:
  METRICS_TOKEN / GITHUB_TOKEN  - GitHub PAT (required)
  GITHUB_USER                   - username (default: icetrahan)
  MAX_REPOS                     - cap repos scanned (default: 500)
  MAX_COMMITS_PER_REPO          - cap commits inspected per repo (default: 300)
  REQUEST_TIMEOUT               - per-request timeout seconds (default: 25)
  SKIP_FORKS                    - "1" to skip forks (default: 1)
  SKIP_ARCHIVED                 - "1" to skip archived (default: 1)
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
MAX_COMMITS_PER_REPO = int(os.environ.get("MAX_COMMITS_PER_REPO", "300"))
REQUEST_TIMEOUT = int(os.environ.get("REQUEST_TIMEOUT", "25"))
SKIP_FORKS = os.environ.get("SKIP_FORKS", "1") == "1"
SKIP_ARCHIVED = os.environ.get("SKIP_ARCHIVED", "1") == "1"

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
# HTTP with timeouts, retries, rate-limit handling
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


def _handle_rate_limit(resp: requests.Response) -> bool:
    """Return True if we should retry after sleeping."""
    remaining = resp.headers.get("X-RateLimit-Remaining")
    if remaining is not None and remaining.isdigit() and int(remaining) == 0:
        reset = int(resp.headers.get("X-RateLimit-Reset", "0") or 0)
        wait = max(5, min(120, reset - int(time.time()))) if reset else 30
        warn(f"rate limit hit, sleeping {wait}s (reset in {max(0, reset - int(time.time()))}s)")
        time.sleep(wait)
        return True
    if resp.status_code in (403, 429):
        retry_after = resp.headers.get("Retry-After")
        wait = int(retry_after) if retry_after and retry_after.isdigit() else 30
        wait = min(wait, 120)
        warn(f"status {resp.status_code}, backing off {wait}s")
        time.sleep(wait)
        return True
    return False


def gh_get(
    path: str,
    params: dict[str, Any] | None = None,
    *,
    max_retries: int = 4,
    quiet: bool = False,
) -> requests.Response | None:
    """GET with timeout, retries, and rate-limit awareness. Returns None on hard failure."""
    url = path if path.startswith("http") else f"{API}{path}"

    for attempt in range(1, max_retries + 1):
        try:
            if not quiet:
                log(f"GET {url[len(API):] if url.startswith(API) else url}  params={params or {}}  (try {attempt}/{max_retries})")
            resp = SESSION.get(url, params=params, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as exc:
            warn(f"network error: {exc!r}; retry in {2 ** attempt}s")
            time.sleep(2 ** attempt)
            continue

        if not quiet:
            rem = resp.headers.get("X-RateLimit-Remaining", "?")
            log(f"  -> {resp.status_code}  rate-remaining={rem}")

        if resp.status_code == 200:
            return resp
        if resp.status_code == 409:  # empty repo
            return resp
        if resp.status_code == 404:
            return resp

        if _handle_rate_limit(resp):
            continue

        if 500 <= resp.status_code < 600:
            warn(f"server error {resp.status_code}; retry in {2 ** attempt}s")
            time.sleep(2 ** attempt)
            continue

        warn(f"unexpected {resp.status_code}: {resp.text[:200]}")
        return resp

    warn(f"giving up on {url} after {max_retries} attempts")
    return None


# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------


def fetch_all_repos() -> list[dict]:
    log(f"Fetching repositories (affiliation=owner,collaborator,organization_member)")
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
        data = resp.json()
        if not data:
            break
        repos.extend(data)
        log(f"  page {page}: +{len(data)} repos (total {len(repos)})")
        if len(repos) >= MAX_REPOS:
            log(f"  MAX_REPOS={MAX_REPOS} reached")
            repos = repos[:MAX_REPOS]
            break
        page += 1
    return repos


def fetch_user_commits_stats(owner: str, repo: str) -> tuple[dict[str, int], int, int]:
    """Fetch per-file additions for commits authored by USER in owner/repo."""
    lang_additions: dict[str, int] = defaultdict(int)
    total_additions = 0
    total_commits = 0

    page = 1
    commit_shas: list[str] = []
    while len(commit_shas) < MAX_COMMITS_PER_REPO:
        resp = gh_get(
            f"/repos/{owner}/{repo}/commits",
            {"author": USERNAME, "per_page": 100, "page": page},
            quiet=True,
        )
        if resp is None:
            break
        if resp.status_code == 409:
            log(f"    {owner}/{repo} is empty")
            return lang_additions, 0, 0
        if resp.status_code != 200:
            break
        commits = resp.json() or []
        if not commits:
            break
        commit_shas.extend(c["sha"] for c in commits)
        page += 1
        if len(commits) < 100:
            break

    commit_shas = commit_shas[:MAX_COMMITS_PER_REPO]
    log(f"    found {len(commit_shas)} commits by {USERNAME}, fetching diffs...")

    for i, sha in enumerate(commit_shas, start=1):
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
        total_commits += 1

        if i % 25 == 0:
            log(f"    ...{i}/{len(commit_shas)} diffs scanned, {total_additions} lines so far")

    return lang_additions, total_additions, total_commits


# ---------------------------------------------------------------------------
# Rendering (unchanged output format)
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
            {
                "name": lang,
                "lines": lines,
                "pct": pct,
                "color": LANG_COLORS.get(lang, "#858585"),
            }
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


def generate_json_stats(lang_stats: dict, total_lines: int, total_commits: int, repo_count: int) -> dict:
    sorted_langs = sorted(lang_stats.items(), key=lambda x: x[1], reverse=True)[:10]
    total = sum(l[1] for l in sorted_langs) or 1
    stats = {
        "total_repos_analyzed": repo_count,
        "total_lines_added": total_lines,
        "total_commits": total_commits,
        "languages": [],
    }
    for lang, lines in sorted_langs:
        pct = (lines / total * 100) if total > 0 else 0
        stats["languages"].append(
            {
                "name": lang,
                "lines_added": lines,
                "percentage": round(pct, 1),
                "color": LANG_COLORS.get(lang, "#858585"),
            }
        )
    return stats


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    if not GITHUB_TOKEN:
        log("FATAL: no GitHub token (METRICS_TOKEN or GITHUB_TOKEN)")
        return 1

    log(f"user={USERNAME}  MAX_REPOS={MAX_REPOS}  MAX_COMMITS_PER_REPO={MAX_COMMITS_PER_REPO}  timeout={REQUEST_TIMEOUT}s")
    log(f"skip_forks={SKIP_FORKS}  skip_archived={SKIP_ARCHIVED}")

    rl = gh_get("/rate_limit")
    if rl is not None and rl.status_code == 200:
        core = rl.json().get("resources", {}).get("core", {})
        log(f"rate limit: {core.get('remaining')}/{core.get('limit')} remaining, "
            f"resets in {max(0, core.get('reset', 0) - int(time.time()))}s")

    repos = fetch_all_repos()
    log(f"Scanning {len(repos)} repositories")

    lang_stats: dict[str, int] = defaultdict(int)
    total_lines = 0
    total_commits = 0
    repos_with_contributions = 0

    for i, repo in enumerate(repos, start=1):
        owner = repo["owner"]["login"]
        name = repo["name"]
        is_fork = repo.get("fork", False)
        is_archived = repo.get("archived", False)

        if SKIP_FORKS and is_fork:
            log(f"[{i}/{len(repos)}] skip fork: {owner}/{name}")
            continue
        if SKIP_ARCHIVED and is_archived:
            log(f"[{i}/{len(repos)}] skip archived: {owner}/{name}")
            continue

        log(f"[{i}/{len(repos)}] scanning {owner}/{name}")
        try:
            repo_langs, repo_lines, repo_commits = fetch_user_commits_stats(owner, name)
        except Exception as exc:
            warn(f"error scanning {owner}/{name}: {exc!r}")
            continue

        if repo_commits > 0:
            repos_with_contributions += 1
            log(f"    OK: {repo_commits} commits, {format_number(repo_lines)} lines")
            for lang, lines in repo_langs.items():
                lang_stats[lang] += lines
            total_lines += repo_lines
            total_commits += repo_commits
        else:
            log(f"    no commits by {USERNAME}")

    log("=" * 60)
    log(f"TOTAL: {format_number(total_lines)} lines across {total_commits} commits")
    log(f"Contributing to {repos_with_contributions}/{len(repos)} repos")

    svg_content = generate_svg(lang_stats, total_lines, total_commits, repos_with_contributions)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    svg_path = os.path.join(script_dir, "..", "code-stats.svg")
    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(svg_content)
    log(f"wrote {svg_path}")

    json_stats = generate_json_stats(lang_stats, total_lines, total_commits, repos_with_contributions)
    json_path = os.path.join(script_dir, "..", "stats.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_stats, f, indent=2)
    log(f"wrote {json_path}")

    log("Contribution breakdown:")
    for lang in json_stats["languages"]:
        log(f"   {lang['name']}: {format_number(lang['lines_added'])} lines ({lang['percentage']}%)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
