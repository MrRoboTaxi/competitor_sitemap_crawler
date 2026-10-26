#!/usr/bin/env python3
"""Monitor new cards in ClipTrend's public template library."""

from __future__ import annotations

import argparse
import json
import logging
import os
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
LIBRARY_URL = "https://cliptrend.ai/templates"
HOST = "cliptrend.ai"
STATE_FILE = BASE_DIR / "state" / "cliptrend_template_urls.txt"
REPORTS_DIR = BASE_DIR / "reports"
USER_AGENT = "cliptrend-template-monitor/1.0"


def fetch_library() -> bytes:
    request = urllib.request.Request(
        LIBRARY_URL,
        headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
    )
    with urllib.request.urlopen(request, timeout=40) as response:
        if response.headers.get_content_type() != "text/html":
            raise RuntimeError("ClipTrend template library did not return HTML")
        return response.read()


def normalize_card_url(href: str) -> str | None:
    parsed = urllib.parse.urlsplit(urllib.parse.urljoin(LIBRARY_URL, href))
    if parsed.scheme != "https" or parsed.netloc.lower() != HOST:
        return None
    path = parsed.path.rstrip("/")
    if not any(path.startswith(prefix) for prefix in ("/templates/", "/ai-video-effects/")):
        return None
    if len(path.split("/")) != 3:
        return None
    # Some listed cards point at a specific preset on a shared template page.
    query = urllib.parse.urlencode(sorted(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)))
    return urllib.parse.urlunsplit(("https", HOST, path, query, ""))


class CardParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.cards: dict[str, dict[str, str]] = {}
        self.active: dict[str, str] | None = None
        self.field: str | None = None
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "a" and (attributes.get("data-analytics-id") or "").startswith("templates_card_open_"):
            href = attributes.get("href") or ""
            url = normalize_card_url(href)
            self.active = {"url": url} if url else None
        elif self.active is not None:
            if tag in {"h2", "p"}:
                self.field = "name" if tag == "h2" else "description"
                self.parts = []
            elif tag == "img" and attributes.get("src"):
                self.active["image"] = attributes["src"] or ""
                if attributes.get("alt"):
                    self.active["name"] = attributes["alt"] or ""
            elif tag == "span" and (attributes.get("title") or "").endswith(" credits"):
                self.active["credits"] = attributes["title"] or ""

    def handle_endtag(self, tag: str) -> None:
        if self.active is None:
            return
        if (tag == "h2" and self.field == "name") or (tag == "p" and self.field == "description"):
            self.active[self.field] = " ".join("".join(self.parts).split())
            self.field = None
            self.parts = []
        elif tag == "a":
            url = self.active["url"]
            self.cards.setdefault(url, {}).update({k: v for k, v in self.active.items() if v})
            self.active = None

    def handle_data(self, data: str) -> None:
        if self.active is not None and self.field is not None:
            self.parts.append(data)


def parse_cards(raw: bytes) -> dict[str, dict[str, str]]:
    parser = CardParser()
    parser.feed(raw.decode("utf-8", errors="replace"))
    if not parser.cards:
        raise RuntimeError("No ClipTrend template cards found; refusing to change the baseline")
    if any(not details.get("name") for details in parser.cards.values()):
        raise RuntimeError("Some ClipTrend template cards have no name; refusing to change the baseline")
    return parser.cards


def load_known_urls() -> set[str] | None:
    if not STATE_FILE.exists():
        return None
    return {line.strip() for line in STATE_FILE.read_text(encoding="utf-8").splitlines() if line.strip()}


def save_known_urls(urls: set[str]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text("\n".join(sorted(urls)) + "\n", encoding="utf-8")


def write_report(cards: list[dict[str, str]]) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    directory = REPORTS_DIR / stamp
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "cliptrend_new_urls.txt").write_text(
        "\n".join(card["url"] for card in cards) + "\n", encoding="utf-8"
    )
    (directory / "cliptrend_new_templates.json").write_text(
        json.dumps({"generated_at": stamp, "count": len(cards), "templates": cards}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return directory


def send_bark(cards: list[dict[str, str]], test: bool = False) -> None:
    key = os.environ.get("BARK_DEVICE_KEY")
    if not key:
        raise RuntimeError("BARK_DEVICE_KEY is required when new ClipTrend templates appear")
    preview = "\n".join(card["name"] for card in cards[:10])
    if len(cards) > 10:
        preview += f"\n…及另外 {len(cards) - 10} 个"
    payload = json.dumps(
        {
            "title": "ClipTrend 监控测试" if test else f"ClipTrend 新增 {len(cards)} 个特效模板",
            "body": "Bark 推送链路正常" if test else preview,
            "url": cards[0]["url"],
            "group": "cliptrend-templates",
            "sound": "minuet",
        },
        ensure_ascii=False,
    ).encode("utf-8")
    base = (os.environ.get("BARK_SERVER_URL") or "https://api.day.app").rstrip("/")
    endpoint = f"{base}/{urllib.parse.quote(key, safe='')}"
    request = urllib.request.Request(
        endpoint,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"Bark returned HTTP {response.status}")
    logging.info("Bark notification sent for %d new ClipTrend templates", len(cards))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bootstrap", action="store_true", help="Record current cards without alerting")
    parser.add_argument("--test-notification", action="store_true", help="Send a Bark test without changing the baseline")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.test_notification:
        send_bark([{"url": LIBRARY_URL, "name": "ClipTrend"}], test=True)
        return 0

    current = parse_cards(fetch_library())
    known = load_known_urls()
    if args.bootstrap or known is None:
        save_known_urls(set(current))
        logging.info("Baseline recorded: %d ClipTrend template cards", len(current))
        return 0
    if len(current) < max(1, len(known) * 0.8):
        raise RuntimeError(f"Template library shrank unexpectedly ({len(known)} -> {len(current)}); preserving baseline")

    new_urls = sorted(set(current) - known)
    logging.info("Current: %d; known: %d; new: %d", len(current), len(known), len(new_urls))
    if new_urls:
        cards = [current[url] for url in new_urls]
        report = write_report(cards)
        logging.info("New-template report: %s", report)
        send_bark(cards)
    save_known_urls(known | set(current))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        logging.exception("ClipTrend monitor failed: %s", error)
        raise SystemExit(1)
