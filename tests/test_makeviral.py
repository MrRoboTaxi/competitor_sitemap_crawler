import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import makeviral_sitemap_crawler as monitor


def sitemap(*paths):
    items = "".join(f"<url><loc>https://www.makeviral.ai{path}</loc></url>" for path in paths)
    return f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</urlset>'.encode()


class MakeViralMonitorTest(unittest.TestCase):
    def test_only_template_detail_urls_are_tracked(self):
        raw = sitemap("/templates", "/templates/new-effect", "/tools/something", "/templates/another/")
        self.assertEqual(
            monitor.parse_sitemap(raw),
            {
                "https://www.makeviral.ai/templates/new-effect",
                "https://www.makeviral.ai/templates/another",
            },
        )
        self.assertIsNone(monitor.normalize_template("https://www.makeviral.ai.evil.org/templates/fake"))

    def test_invalid_or_empty_sitemap_does_not_replace_state(self):
        for raw in (b"<html>", sitemap("/tools/something")):
            with self.assertRaises(RuntimeError):
                monitor.parse_sitemap(raw)

    def test_notification_check_does_not_fetch_or_change_state(self):
        with patch.object(monitor, "fetch") as fetch, patch.object(monitor, "send_bark") as bark:
            self.assertEqual(monitor.main(["--test-notification"]), 0)
            fetch.assert_not_called()
            self.assertTrue(bark.call_args.kwargs["test"])

    def test_new_template_pushes_before_state_advances(self):
        old = "https://www.makeviral.ai/templates/old"
        new = "https://www.makeviral.ai/templates/new"
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp) / "known.txt"
            state.write_text(old + "\n", encoding="utf-8")
            with (
                patch.object(monitor, "STATE_FILE", state),
                patch.object(monitor, "REPORTS_DIR", Path(temp) / "reports"),
                patch.object(monitor, "sitemap_url", return_value=monitor.SITEMAP_FALLBACK),
                patch.object(monitor, "fetch", return_value=sitemap("/templates/old", "/templates/new")),
                patch.object(monitor, "page_details", return_value={"url": new, "slug": "new", "name": "New effect"}),
                patch.object(monitor, "send_bark", side_effect=RuntimeError("Bark unavailable")),
            ):
                with self.assertRaisesRegex(RuntimeError, "Bark unavailable"):
                    monitor.main([])
                self.assertEqual(state.read_text(encoding="utf-8"), old + "\n")
            with (
                patch.object(monitor, "STATE_FILE", state),
                patch.object(monitor, "REPORTS_DIR", Path(temp) / "reports"),
                patch.object(monitor, "sitemap_url", return_value=monitor.SITEMAP_FALLBACK),
                patch.object(monitor, "fetch", return_value=sitemap("/templates/old", "/templates/new")),
                patch.object(monitor, "page_details", return_value={"url": new, "slug": "new", "name": "New effect"}),
                patch.object(monitor, "send_bark") as bark,
            ):
                self.assertEqual(monitor.main([]), 0)
                bark.assert_called_once()
                self.assertEqual(set(state.read_text(encoding="utf-8").splitlines()), {old, new})
                report = next((Path(temp) / "reports").glob("*/makeviral_new_templates.json"))
                self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["count"], 1)


if __name__ == "__main__":
    unittest.main()
