#!/usr/bin/env python3
"""Monitor newly published MakeViral trend templates and notify via Bark."""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ROBOTS_URL = "https://www.makeviral.ai/robots.txt"
SITEMAP_FALLBACK = "https://www.makeviral.ai/sitemap.xml"
TEMPLATE_PREFIX = "/templates/"
HOST = "www.makeviral.ai"
STATE_FILE = BASE_DIR / "state" / "makeviral_urls.txt"
REPORTS_DIR = BASE_DIR / "reports"
USER_AGENT = "makeviral-sitemap-monitor/1.0"


def fetch(url: str, timeout: int = 30) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def sitemap_url() -> str:
    """Use the sitemap advertised by MakeViral, with its known URL as fallback."""
    try:
        robots = fetch(ROBOTS_URL).decode("utf-8", errors="replace")
    except (OSError, TimeoutError) as error:
        logging.warning("Cannot read robots.txt: %s", error)
        return SITEMAP_FALLBACK
    for line in robots.splitlines():
        match = re.match(r"\s*sitemap\s*:\s*(\S+)", line, re.I)
        if match:
            candidate = urllib.parse.urlsplit(match.group(1))
            if candidate.scheme == "https" and candidate.netloc.lower() == HOST:
                return match.group(1)
    return SITEMAP_FALLBACK


def normalize_template(url: str) -> str | None:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc.lower() != HOST:
        return None
    path = parsed.path.rstrip("/")
    if not path.startswith(TEMPLATE_PREFIX) or path == TEMPLATE_PREFIX.rstrip("/"):
        return None
    if "/" in path[len(TEMPLATE_PREFIX) :]:
        return None
    return urllib.parse.urlunsplit(("https", HOST, path, "", ""))


def parse_sitemap(raw: bytes) -> set[str]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise RuntimeError(f"Invalid sitemap XML: {error}") from error
    if root.tag.rsplit("}", 1)[-1] != "urlset":
        raise RuntimeError(f"Unexpected sitemap root: {root.tag}")
    namespace = root.tag.split("}", 1)[0] + "}" if "}" in root.tag else ""
    urls = {
        normalized
        for item in root.findall(f"{namespace}url")
        if (loc := item.findtext(f"{namespace}loc"))
        if (normalized := normalize_template(loc.strip()))
    }
    if not urls:
        raise RuntimeError("No MakeViral templates found; refusing to change the baseline")
    return urls


class MetadataParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_title = False
        self.in_json_ld = False
        self.title: list[str] = []
        self.json_ld: list[str] = []
        self.metadata: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "title":
            self.in_title = True
        elif tag == "script" and attributes.get("type") == "application/ld+json":
            self.in_json_ld = True
        elif tag == "meta":
            name = attributes.get("name") or attributes.get("property")
            if name in {"description", "og:image"}:
                self.metadata[name] = attributes.get("content") or ""
        elif tag == "link" and attributes.get("rel") == "canonical":
            self.metadata["canonical"] = attributes.get("href") or ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
        elif tag == "script":
            self.in_json_ld = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title.append(data)
        elif self.in_json_ld:
            self.json_ld.append(data)

    def result(self) -> dict[str, str]:
        result = {
            "title": " ".join("".join(self.title).split()),
            "description": self.metadata.get("description", ""),
            "image": self.metadata.get("og:image", ""),
            "canonical": self.metadata.get("canonical", ""),
        }
        for block in self.json_ld:
            try:
                data = json.loads(block)
            except json.JSONDecodeError:
                continue
            entries = data if isinstance(data, list) else [data]
            for entry in entries:
                if isinstance(entry, dict) and entry.get("@type") == "Article":
                    result["name"] = entry.get("headline", "")
                    result["date_published"] = entry.get("datePublished", "")
                    result["date_modified"] = entry.get("dateModified", "")
                    return result
        return result


def page_details(url: str) -> dict[str, str]:
    parser = MetadataParser()
    parser.feed(fetch(url).decode("utf-8", errors="replace"))
    details = parser.result()
    if details["canonical"] and details["canonical"].rstrip("/") != url:
        raise RuntimeError(f"Unexpected canonical URL for {url}: {details['canonical']}")
    if not details["title"]:
        raise RuntimeError(f"Template page has no title: {url}")
    return {"url": url, "slug": url.rsplit("/", 1)[-1], **details}


def load_known_urls() -> set[str] | None:
    if not STATE_FILE.exists():
        return None
    return {line.strip() for line in STATE_FILE.read_text(encoding="utf-8").splitlines() if line.strip()}


def save_known_urls(urls: set[str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text("\n".join(sorted(urls)) + "\n", encoding="utf-8")


def write_report(pages: list[dict[str, str]]) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    directory = REPORTS_DIR / stamp
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "makeviral_new_urls.txt").write_text(
        "\n".join(page["url"] for page in pages) + "\n", encoding="utf-8"
    )
    (directory / "makeviral_new_templates.json").write_text(
        json.dumps({"generated_at": stamp, "count": len(pages), "templates": pages}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return directory


def send_bark(pages: list[dict[str, str]], test: bool = False) -> None:
    if not pages:
        return
    key = os.environ.get("BARK_DEVICE_KEY")
    if not key:
        raise RuntimeError("BARK_DEVICE_KEY is required when new templates appear")
    base = (os.environ.get("BARK_SERVER_URL") or "https://api.day.app").rstrip("/")
    endpoint = f"{base}/{urllib.parse.quote(key, safe='')}"
    total_parts = (len(pages) + 11) // 12
    for part, start in enumerate(range(0, len(pages), 12), 1):
        chunk = pages[start:start + 12]
        title = "MakeViral 监控测试" if test else f"MakeViral 新增 {len(pages)} 个特效模板"
        if total_parts > 1:
            title += f"（{part}/{total_parts}）"
        payload = json.dumps(
            {"title": title, "body": "Bark 推送链路正常" if test else "\n".join(page.get("name") or page["slug"] for page in chunk),
             "url": chunk[0]["url"], "group": "makeviral-templates", "sound": "minuet"},
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(endpoint, data=payload, headers={"Content-Type": "application/json", "User-Agent": USER_AGENT}, method="POST")
        with urllib.request.urlopen(request, timeout=20) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"Bark returned HTTP {response.status}")
        logging.info("Bark notification sent for MakeViral (%d/%d, %d templates)", part, total_parts, len(chunk))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap", action="store_true", help="Record the current templates without alerting")
    parser.add_argument("--test-notification", action="store_true", help="Send a Bark test without changing the baseline")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.test_notification:
        send_bark([{"url": "https://www.makeviral.ai/templates", "slug": "templates"}], test=True)
        return 0

    current = parse_sitemap(fetch(sitemap_url()))
    known = load_known_urls()
    if args.bootstrap or known is None:
        save_known_urls(current)
        logging.info("Baseline recorded: %d MakeViral templates", len(current))
        return 0
    if len(current) < max(1, len(known) * 0.8):
        raise RuntimeError(f"Sitemap shrank unexpectedly ({len(known)} -> {len(current)}); preserving baseline")

    new_urls = sorted(current - known)
    logging.info("Current: %d; known: %d; new: %d", len(current), len(known), len(new_urls))
    if new_urls:
        pages = [page_details(url) for url in new_urls]
        report = write_report(pages)
        logging.info("New-template report: %s", report)
        send_bark(pages)
    # Keep historical URLs so a removed and later restored template is not announced twice.
    save_known_urls(known | current)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        logging.exception("MakeViral monitor failed: %s", error)
        raise SystemExit(1)
