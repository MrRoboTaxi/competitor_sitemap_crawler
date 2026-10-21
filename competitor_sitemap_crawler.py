#!/usr/bin/env python3
"""Monitor newly published English/default-language pages on selected competitors."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STATE_DIR = BASE_DIR / "state"
REPORTS_DIR = BASE_DIR / "reports"
USER_AGENT = "competitor-sitemap-crawler/1.0 (+https://github.com/MrRoboTaxi/competitor_sitemap_crawler)"
LOCALE_PREFIXES = frozenset({"en", "ja", "jp", "ko", "zh", "zh-tw", "zh-hant", "tw", "de", "pt", "it", "fr", "es", "br", "ru", "ms", "id", "tr", "pl", "nl", "sv", "ph"})

SITES = {
    "aireel": {"name": "Aireel", "host": "www.aireel.net", "fallback": "https://www.aireel.net/sitemap_index.xml"},
    "domoai": {"name": "DomoAI", "host": "www.domoai.app", "fallback": "https://www.domoai.app/sitemap.xml"},
    "easemate": {"name": "EaseMate", "host": "www.easemate.ai", "fallback": "https://www.easemate.ai/sitemap.xml"},
    "kapwing": {"name": "Kapwing", "host": "www.kapwing.com", "fallback": "https://www.kapwing.com/sitemap-index.xml"},
    "viddo": {"name": "Viddo", "host": "viddo.ai", "fallback": "https://viddo.ai/sitemap.xml"},
    "supermaker": {"name": "Supermaker", "host": "supermaker.ai", "fallback": "https://supermaker.ai/sitemap.xml"},
    "pippit": {"name": "Pippit", "host": "www.pippit.ai", "fallback": "https://www.pippit.ai/sitemap.xml"},
}


def fetch(url: str, timeout: int = 45) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def root_sitemaps(site: dict[str, str]) -> list[str]:
    robots_url = f"https://{site['host']}/robots.txt"
    try:
        robots = fetch(robots_url).decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as error:
        logging.warning("%s: cannot read robots.txt: %s", site["name"], error)
        return [site["fallback"]]
    urls = [match.group(1) for line in robots.splitlines() if (match := re.match(r"\s*sitemap\s*:\s*(\S+)", line, re.I))]
    own_host = site["host"].lower()
    urls = [url for url in urls if urllib.parse.urlsplit(url).netloc.lower() == own_host]
    return urls or [site["fallback"]]


def should_follow_sitemap(site_id: str, url: str) -> bool:
    """Pippit advertises many translated sitemap branches; keep default and English."""
    if site_id != "pippit":
        return True
    path = urllib.parse.urlsplit(url).path
    return "/default/" in path or "/kep-en/" in path or path.endswith("/sitemap.xml") or path.endswith("sitemap_index.xml")


def parse_sitemap(site_id: str, url: str, visited: set[str]) -> set[str]:
    if url in visited:
        return set()
    visited.add(url)
    logging.info("Fetching sitemap: %s", url)
    try:
        root = ET.fromstring(fetch(url))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            logging.warning("Skipping missing sitemap %s", url)
            return set()
        raise RuntimeError(f"Could not parse sitemap {url}: {error}") from error
    except (urllib.error.URLError, TimeoutError, ET.ParseError) as error:
        raise RuntimeError(f"Could not parse sitemap {url}: {error}") from error
    namespace = "{" + root.tag.split("}", 1)[0].lstrip("{") + "}" if "}" in root.tag else ""
    kind = root.tag.rsplit("}", 1)[-1]
    if kind == "sitemapindex":
        return set().union(*(parse_sitemap(site_id, loc.strip(), visited) for item in root.findall(f"{namespace}sitemap") if (loc := item.findtext(f"{namespace}loc")) and should_follow_sitemap(site_id, loc.strip())))
    if kind != "urlset":
        raise RuntimeError(f"Unexpected XML root {kind} from {url}")
    return {loc.strip() for item in root.findall(f"{namespace}url") if (loc := item.findtext(f"{namespace}loc"))}


def normalize_target(url: str, host: str) -> str | None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != host:
        return None
    path = parsed.path.rstrip("/") or "/"
    first_segment = path.lstrip("/").split("/", 1)[0].lower()
    if first_segment in LOCALE_PREFIXES:
        return None
    return urllib.parse.urlunsplit(("https", host, path, "", ""))


def collect_urls(site_id: str, site: dict[str, str]) -> list[str]:
    all_urls: set[str] = set()
    visited: set[str] = set()
    for sitemap in root_sitemaps(site):
        all_urls.update(parse_sitemap(site_id, sitemap, visited))
    urls = {normalized for url in all_urls if (normalized := normalize_target(url, site["host"]))}
    if not urls:
        raise RuntimeError(f"{site['name']}: no target URLs found; refusing to overwrite state")
    return sorted(urls)


def state_file(site_id: str) -> Path:
    return STATE_DIR / f"{site_id}_urls.txt"


def load_known_urls(site_id: str) -> set[str]:
    path = state_file(site_id)
    return set(path.read_text(encoding="utf-8").splitlines()) if path.exists() else set()


def write_report(site_id: str, site: dict[str, str], new_urls: list[str]) -> None:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    report_dir = REPORTS_DIR / stamp
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / f"{site_id}_new_urls.txt").write_text("\n".join(new_urls) + "\n", encoding="utf-8")
    (report_dir / f"{site_id}_summary.json").write_text(json.dumps({"site": site["name"], "count": len(new_urls), "urls": new_urls}, indent=2) + "\n", encoding="utf-8")


def send_bark(site_id: str, site: dict[str, str], new_urls: list[str]) -> None:
    key = os.environ.get("BARK_DEVICE_KEY")
    if not key:
        raise RuntimeError("BARK_DEVICE_KEY is required for scheduled monitoring")
    preview = "\n".join(url.rsplit("/", 1)[-1] or "/" for url in new_urls[:12])
    if len(new_urls) > 12:
        preview += f"\n…及另外 {len(new_urls) - 12} 条"
    payload = json.dumps({"title": f"{site['name']} 新增 {len(new_urls)} 个页面", "body": preview, "group": f"{site_id}-sitemap", "sound": "minuet"}, ensure_ascii=False).encode()
    base_url = (os.environ.get("BARK_SERVER_URL") or "https://api.day.app").rstrip("/")
    request = urllib.request.Request(f"{base_url}/{urllib.parse.quote(key, safe='')}", data=payload, headers={"Content-Type": "application/json", "User-Agent": USER_AGENT}, method="POST")
    with urllib.request.urlopen(request, timeout=20) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"Bark returned HTTP {response.status}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap", action="store_true", help="Write baselines without sending alerts.")
    parser.add_argument("--site", action="append", choices=SITES, help="Limit to one or more configured sites.")
    parser.add_argument("--test-notification", action="store_true", help="Send a Bark test alert and exit.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    selected = args.site or list(SITES)
    if args.test_notification:
        send_bark("competitor-sitemap", {"name": "Competitor sitemap monitor"}, ["https://example.com/monitor-test"])
        return 0
    current = {site_id: collect_urls(site_id, SITES[site_id]) for site_id in selected}
    known = {site_id: load_known_urls(site_id) for site_id in selected}
    if args.bootstrap or any(not known[site_id] for site_id in selected):
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        for site_id in selected:
            state_file(site_id).write_text("\n".join(current[site_id]) + "\n", encoding="utf-8")
            logging.info("%s baseline: %d URLs", SITES[site_id]["name"], len(current[site_id]))
        return 0
    changes = {site_id: sorted(set(current[site_id]) - known[site_id]) for site_id in selected}
    for site_id in selected:
        logging.info("%s current: %d; new: %d", SITES[site_id]["name"], len(current[site_id]), len(changes[site_id]))
        if changes[site_id]:
            write_report(site_id, SITES[site_id], changes[site_id])
            send_bark(site_id, SITES[site_id], changes[site_id])
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    for site_id in selected:
        state_file(site_id).write_text("\n".join(current[site_id]) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
