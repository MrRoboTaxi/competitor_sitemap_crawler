import unittest
from unittest.mock import patch

from competitor_sitemap_crawler import collect_urls, normalize_target


class NormalizeTargetTest(unittest.TestCase):
    def test_removes_query_and_fragment(self):
        self.assertEqual(normalize_target("https://viddo.ai/blog/new/?source=x#top", "viddo.ai"), "https://viddo.ai/blog/new")

    def test_excludes_locale_and_other_host(self):
        self.assertIsNone(normalize_target("https://www.aireel.net/ja/feature", "www.aireel.net"))
        self.assertIsNone(normalize_target("https://example.com/page", "www.aireel.net"))

    def test_collect_urls_passes_the_site_id_to_sitemap_parser(self):
        site = {"name": "Mitte", "host": "mitte.ai", "fallback": "https://mitte.ai/sitemap.xml"}
        with patch("competitor_sitemap_crawler.root_sitemaps", return_value=[site["fallback"]]), patch(
            "competitor_sitemap_crawler.parse_sitemap", return_value={"https://mitte.ai/new-page"}
        ) as parser:
            self.assertEqual(collect_urls("mitte", site), ["https://mitte.ai/new-page"])
        parser.assert_called_once_with("mitte", site["fallback"], set())


if __name__ == "__main__":
    unittest.main()
