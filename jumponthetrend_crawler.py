#!/usr/bin/env python3
"""Monitor Jump on the Trend discoveries and launches."""

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
HOST = "jumponthetrend.app"
LIST_URL = f"https://{HOST}/trends/"
SITEMAP_INDEX = f"https://{HOST}/sitemap.xml"
STATE_FILE = BASE_DIR / "state" / "jumponthetrend_trends.json"
REPORTS_DIR = BASE_DIR / "reports"
USER_AGENT = "Mozilla/5.0 (compatible; JumpOnTheTrendMonitor/1.0)"
STATUS_RANK = {"rising": 0, "sitemap_only": 1, "coming_soon": 2, "active": 3}
STATUS_LABEL = {
    "rising": "趋势线索",
    "sitemap_only": "新趋势页",
    "coming_soon": "即将上线",
    "active": "已上线",
}


def fetch(url: str, expected_type: str) -> bytes:
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        if response.headers.get_content_type() != expected_type:
            raise RuntimeError(f"Unexpected content type from {url}: {response.headers.get_content_type()}")
        return response.read()


def normalize_trend_url(href: str) -> str | None:
    parsed = urllib.parse.urlsplit(urllib.parse.urljoin(LIST_URL, href))
    if parsed.scheme != "https" or parsed.netloc.lower() != HOST:
        return None
    if not re.fullmatch(r"/trends/[^/]+/?", parsed.path):
        return None
    return f"https://{HOST}{parsed.path.rstrip('/')}/"


def find_trends_sitemap(raw: bytes) -> str:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise RuntimeError(f"Invalid sitemap index: {error}") from error
    if root.tag.rsplit("}", 1)[-1] != "sitemapindex":
        raise RuntimeError(f"Unexpected sitemap index root: {root.tag}")
    namespace = root.tag.split("}", 1)[0] + "}" if "}" in root.tag else ""
    for item in root.findall(f"{namespace}sitemap"):
        url = (item.findtext(f"{namespace}loc") or "").strip()
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme == "https" and parsed.netloc.lower() == HOST and parsed.path == "/sitemaps/trends.xml":
            return url
    raise RuntimeError("The advertised sitemap index has no trends.xml")


def parse_trends_sitemap(raw: bytes) -> set[str]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as error:
        raise RuntimeError(f"Invalid trends sitemap: {error}") from error
    if root.tag.rsplit("}", 1)[-1] != "urlset":
        raise RuntimeError(f"Unexpected trends sitemap root: {root.tag}")
    namespace = root.tag.split("}", 1)[0] + "}" if "}" in root.tag else ""
    urls = {
        normalized
        for item in root.findall(f"{namespace}url")
        if (loc := item.findtext(f"{namespace}loc"))
        if (normalized := normalize_trend_url(loc.strip()))
    }
    if not urls:
        raise RuntimeError("No trend pages in sitemap; refusing to change the baseline")
    return urls


class TrendListParser(HTMLParser):
    """Read active cards and the two early-stage navigation lists."""

    def __init__(self) -> None:
        super().__init__()
        self.gallery_depth = 0
        self.navigation: str | None = None
        self.anchor: dict[str, str] | None = None
        self.field: str | None = None
        self.parts: list[str] = []
        self.records: dict[str, dict[str, str]] = {}
        self.counts = {"active": 0, "coming_soon": 0, "rising": 0}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "div":
            if self.gallery_depth:
                self.gallery_depth += 1
            elif attributes.get("id") == "gallery":
                self.gallery_depth = 1
        elif tag == "nav":
            label = attributes.get("aria-label")
            if label == "Coming soon":
                self.navigation = "coming_soon"
            elif label == "Rising, not on the site yet":
                self.navigation = "rising"

        if tag == "a":
            url = normalize_trend_url(attributes.get("href") or "")
            classes = (attributes.get("class") or "").split()
            if url and self.gallery_depth and "tc" in classes:
                self.anchor = {"url": url, "status": "active"}
            elif url and self.navigation:
                self.anchor = {"url": url, "status": self.navigation}
        elif self.anchor is not None:
            if tag == "b" and self.anchor["status"] == "active":
                self.field = "name"
                self.parts = []
            elif tag == "span" and "sc" in (attributes.get("class") or "").split():
                self.field = "description"
                self.parts = []
            elif tag == "img" and attributes.get("src"):
                self.anchor["image"] = urllib.parse.urljoin(LIST_URL, attributes["src"] or "")

    def handle_endtag(self, tag: str) -> None:
        if self.anchor is not None:
            if (tag == "b" and self.field == "name") or (tag == "span" and self.field == "description"):
                self.anchor[self.field] = " ".join("".join(self.parts).split())
                self.field = None
                self.parts = []
            elif tag == "a":
                if self.anchor["status"] != "active":
                    self.anchor["name"] = " ".join("".join(self.parts).split())
                record = self.anchor
                self.counts[record["status"]] += 1
                existing = self.records.get(record["url"])
                if not existing or STATUS_RANK[record["status"]] > STATUS_RANK[existing["status"]]:
                    self.records[record["url"]] = record
                self.anchor = None
                self.field = None
                self.parts = []
        if tag == "div" and self.gallery_depth:
            self.gallery_depth -= 1
        elif tag == "nav":
            self.navigation = None

    def handle_data(self, data: str) -> None:
        if self.anchor is not None and (self.field is not None or self.anchor["status"] != "active"):
            self.parts.append(data)


def parse_trend_list(raw: bytes) -> tuple[dict[str, dict[str, str]], dict[str, int]]:
    parser = TrendListParser()
    parser.feed(raw.decode("utf-8", errors="replace"))
    if not parser.counts["active"]:
        raise RuntimeError("No active trend cards found; refusing to change the baseline")
    if any(not record.get("name") for record in parser.records.values()):
        raise RuntimeError("A trend list entry has no name; refusing to change the baseline")
    return parser.records, parser.counts


def collect_trends() -> tuple[dict[str, dict[str, str]], dict[str, int]]:
    sitemap_url = find_trends_sitemap(fetch(SITEMAP_INDEX, "application/xml"))
    sitemap_urls = parse_trends_sitemap(fetch(sitemap_url, "application/xml"))
    listed, counts = parse_trend_list(fetch(LIST_URL, "text/html"))
    records = {url: {"url": url, "status": "sitemap_only", "name": url.rstrip("/").rsplit("/", 1)[-1]} for url in sitemap_urls}
    records.update(listed)
    counts["sitemap"] = len(sitemap_urls)
    counts["total"] = len(records)
    return records, counts


def load_known() -> dict[str, str] | None:
    if not STATE_FILE.exists():
        return None
    data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or any(value not in STATUS_RANK for value in data.values()):
        raise RuntimeError("Invalid Jump on the Trend baseline")
    return data


def save_known(known: dict[str, str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(dict(sorted(known.items())), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def changes(current: dict[str, dict[str, str]], known: dict[str, str]) -> list[dict[str, str]]:
    events = []
    for url, record in sorted(current.items()):
        previous = known.get(url)
        if previous is None or STATUS_RANK[record["status"]] > STATUS_RANK[previous]:
            events.append({**record, "previous_status": previous or "", "event": "new" if previous is None else "upgrade"})
    return events


def write_report(events: list[dict[str, str]], counts: dict[str, int]) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    directory = REPORTS_DIR / stamp
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "jumponthetrend_changes.json").write_text(
        json.dumps({"generated_at": stamp, "counts": counts, "events": events}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return directory


def send_bark(events: list[dict[str, str]], test: bool = False) -> None:
    if not events:
        return
    key = os.environ.get("BARK_DEVICE_KEY")
    if not key:
        raise RuntimeError("BARK_DEVICE_KEY is required when Jump on the Trend changes appear")
    new_count = sum(event["event"] == "new" for event in events)
    upgrades = len(events) - new_count
    base = (os.environ.get("BARK_SERVER_URL") or "https://api.day.app").rstrip("/")
    total_parts = (len(events) + 11) // 12
    for part, start in enumerate(range(0, len(events), 12), 1):
        chunk = events[start:start + 12]
        title = "Jump on the Trend 监控测试" if test else f"Jump on the Trend：新增 {new_count}，进展 {upgrades}"
        if total_parts > 1:
            title += f"（{part}/{total_parts}）"
        payload = json.dumps(
            {"title": title, "body": "Bark 推送链路正常" if test else "\n".join(f"{event['name']} · {STATUS_LABEL[event['status']]}" for event in chunk),
             "url": chunk[0]["url"], "group": "jumponthetrend-trends", "sound": "minuet"},
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(f"{base}/{urllib.parse.quote(key, safe='')}", data=payload, headers={"Content-Type": "application/json", "User-Agent": USER_AGENT}, method="POST")
        with urllib.request.urlopen(request, timeout=20) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"Bark returned HTTP {response.status}")
        logging.info("Bark %s notification sent (%d/%d, %d events)", "test" if test else "trend", part, total_parts, len(chunk))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap", action="store_true", help="Record current trends without alerting")
    parser.add_argument("--test-notification", action="store_true", help="Send a Bark test without changing the baseline")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if args.test_notification:
        send_bark([{"url": LIST_URL, "name": "Jump on the Trend", "status": "active", "event": "new"}], test=True)
        return 0

    current, counts = collect_trends()
    known = load_known()
    if args.bootstrap or known is None:
        save_known({url: record["status"] for url, record in current.items()})
        logging.info("Baseline recorded: %s", counts)
        return 0
    active_known = sum(status == "active" for status in known.values())
    if counts["active"] < max(1, active_known * 0.8) or counts["sitemap"] < max(1, len(known) * 0.8):
        raise RuntimeError(f"Trend sources shrank unexpectedly: {counts}; preserving baseline")

    events = changes(current, known)
    logging.info("Trend counts: %s; new or upgraded: %d", counts, len(events))
    if events:
        report = write_report(events, counts)
        logging.info("Trend report: %s", report)
        send_bark(events)
    for url, record in current.items():
        if url not in known or STATUS_RANK[record["status"]] > STATUS_RANK[known[url]]:
            known[url] = record["status"]
    save_known(known)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        logging.exception("Jump on the Trend monitor failed: %s", error)
        raise SystemExit(1)
