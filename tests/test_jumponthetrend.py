import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import jumponthetrend_crawler as monitor


def sitemap(*paths):
    items = "".join(f"<url><loc>https://jumponthetrend.app{path}</loc></url>" for path in paths)
    return f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</urlset>'.encode()


LIST_HTML = b'''
<div id="gallery"><a class="tc" href="/trends/active/">
<img src="/active.jpg"><b>Active Trend</b><span class="sc">Ready to make.</span></a>
<a class="tc" href="/outside-tool/"><b>Tool</b></a></div>
<nav aria-label="Coming soon"><a href="/trends/soon/">Soon Trend</a></nav>
<nav aria-label="Rising, not on the site yet"><a href="/trends/rising/">Rising Trend</a></nav>
'''


class JumpOnTheTrendMonitorTest(unittest.TestCase):
    def test_sitemap_ignores_tools_and_other_hosts(self):
        raw = sitemap("/trends/active/", "/make-photo-sing/", "/trends/cars/")
        self.assertIn("https://jumponthetrend.app/trends/active/", monitor.parse_trends_sitemap(raw))
        self.assertNotIn("https://jumponthetrend.app/make-photo-sing/", monitor.parse_trends_sitemap(raw))
        self.assertIsNone(monitor.normalize_trend_url("https://evil.example/trends/fake/"))

    def test_gallery_and_prelaunch_statuses_are_distinct(self):
        records, counts = monitor.parse_trend_list(LIST_HTML)
        self.assertEqual(counts, {"active": 1, "coming_soon": 1, "rising": 1})
        self.assertEqual(records["https://jumponthetrend.app/trends/active/"]["name"], "Active Trend")
        self.assertEqual(records["https://jumponthetrend.app/trends/soon/"]["status"], "coming_soon")
        self.assertEqual(records["https://jumponthetrend.app/trends/rising/"]["status"], "rising")

    def test_stage_upgrade_is_reported_once(self):
        url = "https://jumponthetrend.app/trends/soon/"
        current = {url: {"url": url, "name": "Soon Trend", "status": "active"}}
        events = monitor.changes(current, {url: "coming_soon"})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "upgrade")
        self.assertEqual(events[0]["previous_status"], "coming_soon")
        self.assertEqual(monitor.changes(current, {url: "active"}), [])

    def test_invalid_sources_do_not_replace_baseline(self):
        with self.assertRaises(RuntimeError):
            monitor.parse_trends_sitemap(b"<html>")
        with self.assertRaises(RuntimeError):
            monitor.parse_trend_list(b"<html>No gallery</html>")

    def test_bark_failure_preserves_new_stage_for_retry(self):
        url = "https://jumponthetrend.app/trends/soon/"
        current = {url: {"url": url, "name": "Soon Trend", "status": "active"}}
        counts = {"active": 1, "coming_soon": 0, "rising": 0, "sitemap": 1, "total": 1}
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "state.json"
            state.write_text(json.dumps({url: "coming_soon"}), encoding="utf-8")
            with (
                patch.object(monitor, "STATE_FILE", state),
                patch.object(monitor, "REPORTS_DIR", Path(temp) / "reports"),
                patch.object(monitor, "collect_trends", return_value=(current, counts)),
                patch.object(monitor, "send_bark", side_effect=RuntimeError("Bark unavailable")),
            ):
                with self.assertRaisesRegex(RuntimeError, "Bark unavailable"):
                    monitor.main([])
                self.assertEqual(json.loads(state.read_text(encoding="utf-8")), {url: "coming_soon"})
            with (
                patch.object(monitor, "STATE_FILE", state),
                patch.object(monitor, "REPORTS_DIR", Path(temp) / "reports"),
                patch.object(monitor, "collect_trends", return_value=(current, counts)),
                patch.object(monitor, "send_bark") as bark,
            ):
                self.assertEqual(monitor.main([]), 0)
                bark.assert_called_once()
                self.assertEqual(json.loads(state.read_text(encoding="utf-8")), {url: "active"})

    def test_notification_check_does_not_fetch(self):
        with patch.object(monitor, "collect_trends") as collect, patch.object(monitor, "send_bark") as bark:
            self.assertEqual(monitor.main(["--test-notification"]), 0)
            collect.assert_not_called()
            self.assertTrue(bark.call_args.kwargs["test"])


if __name__ == "__main__":
    unittest.main()
