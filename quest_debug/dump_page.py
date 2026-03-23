"""
Run this standalone to dump a FinalBastion page for debugging.
Usage: python dump_page.py [url]
Saves:  quest_debug/<worldname>_raw.html
        quest_debug/<worldname>_plain.txt
        quest_debug/<worldname>_structure.txt
"""

import sys
import re
import os
import requests
from bs4 import BeautifulSoup, Tag, NavigableString

OUT_DIR = os.path.dirname(os.path.abspath(__file__))

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://finalbastion.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

DEFAULT_URL = "https://finalbastion.com/wizard101-guides/w101-quest-guides/mirage-main-quest-line-guide/"

def slug(url):
    parts = [p for p in url.rstrip("/").split("/") if p]
    return re.sub(r"[^a-z0-9_-]", "_", parts[-1].lower())[:40] if parts else "page"

def dump(url):
    print(f"Fetching: {url}")
    resp = requests.get(url, headers=HEADERS, timeout=25)
    resp.raise_for_status()
    print(f"Status: {resp.status_code}  ({len(resp.text)} bytes)")

    name = slug(url)

    # 1) Raw HTML
    raw_path = os.path.join(OUT_DIR, f"{name}_raw.html")
    with open(raw_path, "w", encoding="utf-8") as f:
        f.write(resp.text)
    print(f"Saved raw HTML → {raw_path}")

    soup = BeautifulSoup(resp.text, "html.parser")
    content = (
        soup.find("div", class_="entry-content")
        or soup.find("div", class_="post-content")
        or soup.find("article")
        or soup.find("main")
        or soup.body
    )

    # 2) Plain text (good for reading the quest list as-is)
    plain_path = os.path.join(OUT_DIR, f"{name}_plain.txt")
    with open(plain_path, "w", encoding="utf-8") as f:
        f.write(f"URL: {url}\n")
        f.write(f"Status: {resp.status_code}\n\n")
        if content:
            f.write(content.get_text(separator="\n", strip=True))
    print(f"Saved plain text → {plain_path}")

    # 3) Structure dump — tag tree with first-line text previews
    struct_path = os.path.join(OUT_DIR, f"{name}_structure.txt")
    lines = [f"URL: {url}\n", f"Status: {resp.status_code}\n\n",
             "=== TAG STRUCTURE (entry-content) ===\n"]

    def walk(el, depth=0):
        indent = "  " * depth
        if isinstance(el, NavigableString):
            text = str(el).strip()
            if text:
                lines.append(f"{indent}TEXT: {text[:120]}\n")
            return
        if not isinstance(el, Tag):
            return
        tag  = el.name.lower()
        cls  = " ".join(el.get("class", []))
        style = (el.get("style") or "")[:60]
        text  = el.get_text(separator=" ", strip=True)[:100]
        lines.append(f"{indent}<{tag}"
                     f"{' class=' + repr(cls) if cls else ''}"
                     f"{' style=' + repr(style) if style else ''}>\n")
        if tag in ("li", "h1", "h2", "h3", "h4", "h5", "p"):
            lines.append(f"{indent}  → {text}\n")
        for child in el.children:
            walk(child, depth + 1)

    if content:
        for child in content.children:
            walk(child, depth=0)

    with open(struct_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"Saved structure → {struct_path}")

    # 4) Quick summary of what lists contain
    print("\n=== QUICK SUMMARY ===")
    for i, lst in enumerate(content.find_all(["ol", "ul"])[:10]):
        lis = lst.find_all("li")
        print(f"  List #{i+1} <{lst.name}> — {len(lis)} items")
        for li in lis[:3]:
            print(f"    {li.get_text(separator=' ', strip=True)[:100]}")
        if len(lis) > 3:
            print(f"    ... ({len(lis) - 3} more)")

if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    dump(url)
