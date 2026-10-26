import json
import os
import unittest
from unittest.mock import patch

import cliptrend_template_crawler
import competitor_sitemap_crawler
import jumponthetrend_crawler
import makeviral_sitemap_crawler


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


def capture(sender):
    requests = []

    def urlopen(request, timeout):
        requests.append(json.loads(request.data))
        return _Response()

    with patch.dict(os.environ, {"BARK_DEVICE_KEY": "test-key"}), patch("urllib.request.urlopen", side_effect=urlopen):
        sender()
    return requests


class BarkBatchingTests(unittest.TestCase):
    def check_chunks(self, sender, expected_lines):
        messages = capture(sender)
        self.assertEqual([len(message["body"].splitlines()) for message in messages], expected_lines)
        if len(messages) > 1:
            self.assertEqual(
                [message["title"].rsplit("（", 1)[-1] for message in messages],
                [f"{index}/{len(messages)}）" for index in range(1, len(messages) + 1)],
            )
        return messages

    def test_generic_competitors_send_every_page(self):
        urls = [f"https://example.com/page-{index:02d}" for index in range(25)]
        messages = self.check_chunks(lambda: competitor_sitemap_crawler.send_bark("sample", {"name": "Sample"}, urls), [12, 12, 1])
        self.assertEqual("\n".join(message["body"] for message in messages).splitlines(), [f"page-{index:02d}" for index in range(25)])

    def test_makeviral_sends_every_template_with_chunk_link(self):
        pages = [{"name": f"Effect {index:02d}", "slug": f"effect-{index}", "url": f"https://www.makeviral.ai/templates/effect-{index}"} for index in range(25)]
        messages = self.check_chunks(lambda: makeviral_sitemap_crawler.send_bark(pages), [12, 12, 1])
        self.assertEqual([message["url"] for message in messages], [pages[index]["url"] for index in (0, 12, 24)])
        self.assertEqual("\n".join(message["body"] for message in messages).splitlines(), [page["name"] for page in pages])

    def test_cliptrend_sends_every_template(self):
        cards = [{"name": f"Card {index:02d}", "url": f"https://cliptrend.ai/templates/{index}"} for index in range(13)]
        messages = self.check_chunks(lambda: cliptrend_template_crawler.send_bark(cards), [12, 1])
        self.assertEqual(messages[1]["url"], cards[12]["url"])

    def test_jumponthetrend_sends_every_change(self):
        events = [{"name": f"Trend {index:02d}", "status": "active", "event": "new", "url": f"https://jumponthetrend.app/trends/{index}/"} for index in range(25)]
        messages = self.check_chunks(lambda: jumponthetrend_crawler.send_bark(events), [12, 12, 1])
        self.assertIn("Trend 24", messages[-1]["body"])

    def test_twelve_items_stay_in_one_message(self):
        cards = [{"name": f"Card {index}", "url": f"https://cliptrend.ai/templates/{index}"} for index in range(12)]
        messages = self.check_chunks(lambda: cliptrend_template_crawler.send_bark(cards), [12])
        self.assertNotIn("（", messages[0]["title"])


if __name__ == "__main__":
    unittest.main()
