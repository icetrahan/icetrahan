#!/usr/bin/env python3
"""
Fetches YOUR personal GitHub contribution statistics across all repositories.
Counts lines YOU added (not repo totals).
"""

import os
import json
import requests
from collections import defaultdict
import time

GITHUB_TOKEN = os.environ.get("METRICS_TOKEN") or os.environ.get("GITHUB_TOKEN")
USERNAME = "icetrahan"

# Language colors (GitHub's official colors)
LANG_COLORS = {
    "Python": "#3572A5",
    "JavaScript": "#f1e05a",
    "TypeScript": "#3178c6",
    "Dart": "#00B4AB",
    "C#": "#178600",
    "Java": "#b07219",
    "HTML": "#e34c26",
    "CSS": "#563d7c",
    "SCSS": "#c6538c",
    "Shell": "#89e051",
    "Dockerfile": "#384d54",
    "SQL": "#e38c00",
    "C++": "#f34b7d",
    "C": "#555555",
    "Go": "#00ADD8",
    "Rust": "#dea584",
    "PHP": "#4F5D95",
    "Ruby": "#701516",
    "Swift": "#F05138",
    "Kotlin": "#A97BFF",
    "Vue": "#41b883",
    "Svelte": "#ff3e00",
    "Markdown": "#083fa1",
    "JSON": "#292929",
    "YAML": "#cb171e",
    "Lua": "#000080",
    "GDScript": "#355570",
}

# File extension to language mapping (ONLY REAL CODE - no config/data files)
EXT_TO_LANG = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".dart": "Dart",
    ".cs": "C#",
    ".java": "Java",
    ".cpp": "C++",
    ".cc": "C++",
    ".cxx": "C++",
    ".hpp": "C++",
    ".h": "C++",
    ".c": "C",
    ".go": "Go",
    ".rs": "Rust",
    ".php": "PHP",
    ".rb": "Ruby",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
    ".vue": "Vue",
    ".svelte": "Svelte",
    ".lua": "Lua",
    ".gd": "GDScript",
    ".sql": "SQL",
    ".sh": "Shell",
    ".bash": "Shell",
}

def get_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

def fetch_all_repos():
    """Fetch all repos the user has access to."""
    repos = []
    page = 1
    while True:
        url = f"https://api.github.com/user/repos?per_page=100&page={page}&affiliation=owner,collaborator,organization_member"
        resp = requests.get(url, headers=get_headers())
        if resp.status_code != 200:
            print(f"Error fetching repos: {resp.status_code}")
            break
        data = resp.json()
        if not data:
            break
        repos.extend(data)
        page += 1
    return repos

def fetch_user_commits_stats(owner, repo):
    """
    Fetch stats for commits authored by the user in a repo.
    Returns total additions by language.
    """
    lang_additions = defaultdict(int)
    total_additions = 0
    total_commits = 0
    
    page = 1
    while True:
        # Get commits by the user
        url = f"https://api.github.com/repos/{owner}/{repo}/commits?author={USERNAME}&per_page=100&page={page}"
        resp = requests.get(url, headers=get_headers())
        
        if resp.status_code == 409:  # Empty repo
            break
        if resp.status_code != 200:
            break
            
        commits = resp.json()
        if not commits:
            break
        
        for commit in commits:
            sha = commit["sha"]
            # Get detailed commit stats
            commit_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{sha}"
            commit_resp = requests.get(commit_url, headers=get_headers())
            
            if commit_resp.status_code != 200:
                continue
                
            commit_data = commit_resp.json()
            
            # Get per-file additions
            if "files" in commit_data:
                for file in commit_data["files"]:
                    filename = file.get("filename", "")
                    additions = file.get("additions", 0)
                    
                    # Determine language from extension
                    ext = os.path.splitext(filename)[1].lower()
                    lang = EXT_TO_LANG.get(ext)
                    
                    if lang and additions > 0:
                        lang_additions[lang] += additions
                        total_additions += additions
            
            total_commits += 1
            
            # Rate limit protection
            if total_commits % 50 == 0:
                time.sleep(0.5)
        
        page += 1
        
        # Limit pages to avoid timeout
        if page > 10:
            break
    
    return lang_additions, total_additions, total_commits

def format_number(num):
    """Format large numbers nicely."""
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M"
    elif num >= 1_000:
        return f"{num / 1_000:.1f}k"
    return str(num)

def generate_svg(lang_stats, total_lines, total_commits, total_repos):
    """Generate a beautiful SVG showing language stats."""
    
    # Sort by lines descending, take top 8
    sorted_langs = sorted(lang_stats.items(), key=lambda x: x[1], reverse=True)[:8]
    
    if not sorted_langs:
        sorted_langs = [("No data yet", 0)]
    
    total = sum(l[1] for l in sorted_langs) or 1
    
    # Calculate percentages
    lang_data = []
    for lang, lines in sorted_langs:
        pct = (lines / total * 100) if total > 0 else 0
        color = LANG_COLORS.get(lang, "#858585")
        lang_data.append({
            "name": lang,
            "lines": lines,
            "pct": pct,
            "color": color
        })
    
    # SVG dimensions
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
  
  <!-- Background -->
  <rect class="bg" width="{width}" height="{height}" rx="6"/>
  
  <!-- Border -->
  <rect x="0.5" y="0.5" width="{width-1}" height="{height-1}" rx="6" fill="none" stroke="#30363d" stroke-width="1"/>
  
  <!-- Title -->
  <text x="{padding}" y="32" class="title">📊 My Code Contributions</text>
  <text x="{padding}" y="52" class="subtitle">{format_number(total_lines)} lines added • {format_number(total_commits)} commits • {total_repos} repos analyzed</text>
'''
    
    y_offset = header_height + 5
    bar_width = width - padding * 2 - 140  # Leave room for stats on right
    
    for lang in lang_data:
        fill_width = max((lang["pct"] / 100) * bar_width, 2)
        
        svg += f'''
  <!-- {lang["name"]} -->
  <g transform="translate({padding}, {y_offset})">
    <!-- Language name with color dot -->
    <circle cx="6" cy="8" r="6" fill="{lang["color"]}"/>
    <text x="18" y="12" class="lang-name">{lang["name"]}</text>
    
    <!-- Bar background -->
    <rect x="0" y="20" width="{bar_width}" height="10" class="bar-bg" rx="5"/>
    
    <!-- Bar fill -->
    <rect x="0" y="20" width="{fill_width}" height="10" fill="{lang["color"]}" rx="5"/>
    
    <!-- Stats on right -->
    <text x="{bar_width + 15}" y="14" class="lang-stats">{format_number(lang["lines"])} lines</text>
    <text x="{bar_width + 15}" y="30" class="lang-stats" style="fill: #58a6ff;">{lang["pct"]:.1f}%</text>
  </g>
'''
        y_offset += bar_height
    
    svg += '</svg>'
    return svg

def generate_json_stats(lang_stats, total_lines, total_commits, repo_count):
    """Generate JSON stats."""
    sorted_langs = sorted(lang_stats.items(), key=lambda x: x[1], reverse=True)[:10]
    
    total = sum(l[1] for l in sorted_langs) or 1
    
    stats = {
        "total_repos_analyzed": repo_count,
        "total_lines_added": total_lines,
        "total_commits": total_commits,
        "languages": []
    }
    
    for lang, lines in sorted_langs:
        pct = (lines / total * 100) if total > 0 else 0
        stats["languages"].append({
            "name": lang,
            "lines_added": lines,
            "percentage": round(pct, 1),
            "color": LANG_COLORS.get(lang, "#858585")
        })
    
    return stats

def main():
    if not GITHUB_TOKEN:
        print("ERROR: No GitHub token found. Set METRICS_TOKEN or GITHUB_TOKEN env var.")
        return
    
    print(f"Fetching repositories for {USERNAME}...")
    repos = fetch_all_repos()
    print(f"Found {len(repos)} repositories")
    
    # Aggregate YOUR contribution stats
    lang_stats = defaultdict(int)
    total_lines = 0
    total_commits = 0
    repos_with_contributions = 0
    
    for i, repo in enumerate(repos):
        owner = repo["owner"]["login"]
        name = repo["name"]
        print(f"  [{i+1}/{len(repos)}] Scanning {owner}/{name}...", end=" ")
        
        repo_langs, repo_lines, repo_commits = fetch_user_commits_stats(owner, name)
        
        if repo_commits > 0:
            repos_with_contributions += 1
            print(f"✓ {repo_commits} commits, {format_number(repo_lines)} lines")
            
            for lang, lines in repo_langs.items():
                lang_stats[lang] += lines
            total_lines += repo_lines
            total_commits += repo_commits
        else:
            print("- no commits")
        
        # Rate limit protection
        time.sleep(0.2)
    
    print(f"\n{'='*50}")
    print(f"Total: {format_number(total_lines)} lines added across {total_commits} commits")
    print(f"Repos with your contributions: {repos_with_contributions}/{len(repos)}")
    
    # Generate SVG
    svg_content = generate_svg(lang_stats, total_lines, total_commits, repos_with_contributions)
    
    # Write SVG file
    script_dir = os.path.dirname(os.path.abspath(__file__))
    svg_path = os.path.join(script_dir, "..", "code-stats.svg")
    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(svg_content)
    print(f"\n✅ Generated {svg_path}")
    
    # Write JSON stats
    json_stats = generate_json_stats(lang_stats, total_lines, total_commits, repos_with_contributions)
    json_path = os.path.join(script_dir, "..", "stats.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_stats, f, indent=2)
    print(f"✅ Generated {json_path}")
    
    # Print summary
    print("\n📊 Your Contribution Breakdown:")
    for lang in json_stats["languages"]:
        print(f"   {lang['name']}: {format_number(lang['lines_added'])} lines | {lang['percentage']}%")

if __name__ == "__main__":
    main()
