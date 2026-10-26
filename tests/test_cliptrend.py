import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cliptrend_template_crawler as monitor


def card(href, name):
    return (
        f'<a data-analytics-id="templates_card_open_{name}" href="{href}">'
        f'<h2>{name}</h2><p>A new effect.</p><span title="20 credits">20c</span></a>'
    )


class ClipTrendMonitorTest(unittest.TestCase):
    def test_only_library_cards_are_tracked(self):
        raw = (
            card("/ai-video-effects/new-effect", "New Effect")
            + card("/templates/dance?preset=fast", "Fast Dance")
            + '<a href="/ai-video-effects/seo-only">SEO page</a>'
            + card("https://evil.example/templates/fake", "Fake")
        ).encode()
        cards = monitor.parse_cards(raw)
        self.assertEqual(
            set(cards),
            {
                "https://cliptrend.ai/ai-video-effects/new-effect",
                "https://cliptrend.ai/templates/dance?preset=fast",
            },
        )
        self.assertEqual(cards["https://cliptrend.ai/templates/dance?preset=fast"]["name"], "Fast Dance")
        self.assertEqual(cards["https://cliptrend.ai/templates/dance?preset=fast"]["credits"], "20 credits")

    def test_invalid_library_does_not_replace_state(self):
        with self.assertRaises(RuntimeError):
            monitor.parse_cards(b"<html><body>No cards</body></html>")

    def test_notification_check_does_not_fetch_or_change_state(self):
        with patch.object(monitor, "fetch_library") as fetch, patch.object(monitor, "send_bark") as bark:
            self.assertEqual(monitor.main(["--test-notification"]), 0)
            fetch.assert_not_called()
            self.assertTrue(bark.call_args.kwargs["test"])

    def test_bark_failure_preserves_new_card_for_retry(self):
        old = "https://cliptrend.ai/ai-video-effects/old"
        new = "https://cliptrend.ai/templates/new?preset=one"
        html = (card("/ai-video-effects/old", "Old") + card("/templates/new?preset=one", "New")).encode()
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "known.txt"
            state.write_text(old + "\n", encoding="utf-8")
            with (
                patch.object(monitor, "STATE_FILE", state),
                patch.object(monitor, "REPORTS_DIR", Path(temp) / "reports"),
                patch.object(monitor, "fetch_library", return_value=html),
                patch.object(monitor, "send_bark", side_effect=RuntimeError("Bark unavailable")),
            ):
                with self.assertRaisesRegex(RuntimeError, "Bark unavailable"):
                    monitor.main([])
                self.assertEqual(state.read_text(encoding="utf-8"), old + "\n")
            with (
                patch.object(monitor, "STATE_FILE", state),
                patch.object(monitor, "REPORTS_DIR", Path(temp) / "reports"),
                patch.object(monitor, "fetch_library", return_value=html),
                patch.object(monitor, "send_bark") as bark,
            ):
                self.assertEqual(monitor.main([]), 0)
                bark.assert_called_once()
                self.assertEqual(set(state.read_text(encoding="utf-8").splitlines()), {old, new})
                report = next((Path(temp) / "reports").glob("*/cliptrend_new_templates.json"))
                self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["count"], 1)


if __name__ == "__main__":
    unittest.main()
