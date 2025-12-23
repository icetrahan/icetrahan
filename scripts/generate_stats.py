#!/usr/bin/env python3
"""
Fetches GitHub language statistics across all repositories and generates an SVG.
"""

import os
import json
import requests
from collections import defaultdict

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
}

def get_headers():
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }

def fetch_all_repos():
    """Fetch all repos the user has access to (owned, collaborator, org member)."""
    repos = []
    
    # Fetch owned repos (including private)
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

def fetch_repo_languages(owner, repo):
    """Fetch language breakdown for a specific repo."""
    url = f"https://api.github.com/repos/{owner}/{repo}/languages"
    resp = requests.get(url, headers=get_headers())
    if resp.status_code == 200:
        return resp.json()
    return {}

def estimate_lines(bytes_count):
    """Rough estimate: ~40 bytes per line on average."""
    return bytes_count // 40

def format_number(num):
    """Format large numbers nicely."""
    if num >= 1_000_000:
        return f"{num / 1_000_000:.1f}M"
    elif num >= 1_000:
        return f"{num / 1_000:.0f}k"
    return str(num)

def generate_svg(lang_stats, total_bytes):
    """Generate a beautiful SVG showing language stats."""
    
    # Sort by bytes descending, take top 8
    sorted_langs = sorted(lang_stats.items(), key=lambda x: x[1], reverse=True)[:8]
    
    # Calculate percentages
    lang_data = []
    for lang, bytes_count in sorted_langs:
        pct = (bytes_count / total_bytes * 100) if total_bytes > 0 else 0
        lines = estimate_lines(bytes_count)
        color = LANG_COLORS.get(lang, "#858585")
        lang_data.append({
            "name": lang,
            "bytes": bytes_count,
            "lines": lines,
            "pct": pct,
            "color": color
        })
    
    # SVG dimensions
    width = 450
    bar_height = 36
    padding = 20
    header_height = 50
    height = header_height + len(lang_data) * bar_height + padding * 2
    
    svg = f'''<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <style>
      @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600&amp;display=swap');
      .bg {{ fill: #0d1117; }}
      .title {{ font-family: 'JetBrains Mono', monospace; font-size: 16px; font-weight: 600; fill: #58a6ff; }}
      .lang-name {{ font-family: 'JetBrains Mono', monospace; font-size: 12px; font-weight: 600; fill: #c9d1d9; }}
      .lang-stats {{ font-family: 'JetBrains Mono', monospace; font-size: 11px; fill: #8b949e; }}
      .bar-bg {{ fill: #21262d; rx: 4; }}
    </style>
  </defs>
  
  <!-- Background -->
  <rect class="bg" width="{width}" height="{height}" rx="8"/>
  
  <!-- Border -->
  <rect x="0.5" y="0.5" width="{width-1}" height="{height-1}" rx="8" fill="none" stroke="#30363d" stroke-width="1"/>
  
  <!-- Title -->
  <text x="{padding}" y="32" class="title">📊 Code Statistics</text>
'''
    
    y_offset = header_height + 10
    bar_width = width - padding * 2 - 160  # Leave room for stats on right
    
    for lang in lang_data:
        fill_width = (lang["pct"] / 100) * bar_width
        
        svg += f'''
  <!-- {lang["name"]} -->
  <g transform="translate({padding}, {y_offset})">
    <!-- Language name -->
    <text x="0" y="12" class="lang-name">{lang["name"]}</text>
    
    <!-- Bar background -->
    <rect x="0" y="18" width="{bar_width}" height="8" class="bar-bg"/>
    
    <!-- Bar fill -->
    <rect x="0" y="18" width="{fill_width}" height="8" fill="{lang["color"]}" rx="4"/>
    
    <!-- Stats -->
    <text x="{bar_width + 10}" y="12" class="lang-stats">{format_number(lang["lines"])} lines</text>
    <text x="{bar_width + 10}" y="26" class="lang-stats">{lang["pct"]:.1f}%</text>
  </g>
'''
        y_offset += bar_height
    
    svg += '</svg>'
    return svg

def generate_json_stats(lang_stats, total_bytes, repo_count):
    """Generate JSON stats for potential README injection."""
    sorted_langs = sorted(lang_stats.items(), key=lambda x: x[1], reverse=True)[:8]
    
    stats = {
        "total_repos": repo_count,
        "total_bytes": total_bytes,
        "total_lines": estimate_lines(total_bytes),
        "languages": []
    }
    
    for lang, bytes_count in sorted_langs:
        pct = (bytes_count / total_bytes * 100) if total_bytes > 0 else 0
        stats["languages"].append({
            "name": lang,
            "bytes": bytes_count,
            "lines": estimate_lines(bytes_count),
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
    
    # Aggregate language stats
    lang_stats = defaultdict(int)
    
    for i, repo in enumerate(repos):
        owner = repo["owner"]["login"]
        name = repo["name"]
        print(f"  [{i+1}/{len(repos)}] Analyzing {owner}/{name}...")
        
        languages = fetch_repo_languages(owner, name)
        for lang, bytes_count in languages.items():
            lang_stats[lang] += bytes_count
    
    total_bytes = sum(lang_stats.values())
    print(f"\nTotal: {format_number(total_bytes)} bytes across {len(lang_stats)} languages")
    
    # Generate SVG
    svg_content = generate_svg(lang_stats, total_bytes)
    
    # Write SVG file
    svg_path = os.path.join(os.path.dirname(__file__), "..", "code-stats.svg")
    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(svg_content)
    print(f"\n✅ Generated {svg_path}")
    
    # Write JSON stats
    json_stats = generate_json_stats(lang_stats, total_bytes, len(repos))
    json_path = os.path.join(os.path.dirname(__file__), "..", "stats.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_stats, f, indent=2)
    print(f"✅ Generated {json_path}")
    
    # Print summary
    print("\n📊 Language Breakdown:")
    for lang in json_stats["languages"]:
        print(f"   {lang['name']}: {format_number(lang['lines'])} lines | {lang['percentage']}%")

if __name__ == "__main__":
    main()
