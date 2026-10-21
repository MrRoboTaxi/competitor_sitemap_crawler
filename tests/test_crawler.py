import unittest
from unittest.mock import patch

from competitor_sitemap_crawler import collect_urls, normalize_target, should_follow_sitemap


class NormalizeTargetTest(unittest.TestCase):
    def test_removes_query_and_fragment(self):
        self.assertEqual(normalize_target("https://viddo.ai/blog/new/?source=x#top", "viddo.ai"), "https://viddo.ai/blog/new")

    def test_excludes_locale_and_other_host(self):
        self.assertIsNone(normalize_target("https://www.aireel.net/ja/feature", "www.aireel.net"))
        self.assertIsNone(normalize_target("https://example.com/page", "www.aireel.net"))

    def test_pippit_only_follows_english_sitemap_branches(self):
        self.assertTrue(should_follow_sitemap("pippit", "https://www.pippit.ai/sitemap/new/1/default/index0.xml"))
        self.assertTrue(should_follow_sitemap("pippit", "https://www.pippit.ai/sitemap/new/1/kep-en/index0.xml"))
        self.assertFalse(should_follow_sitemap("pippit", "https://www.pippit.ai/sitemap/new/1/kep-es-es/index0.xml"))

    def test_collect_urls_passes_the_site_id_to_sitemap_parser(self):
        site = {"name": "Pippit", "host": "www.pippit.ai", "fallback": "https://www.pippit.ai/sitemap.xml"}
        with patch("competitor_sitemap_crawler.root_sitemaps", return_value=[site["fallback"]]), patch(
            "competitor_sitemap_crawler.parse_sitemap", return_value={"https://www.pippit.ai/new-page"}
        ) as parser:
            self.assertEqual(collect_urls("pippit", site), ["https://www.pippit.ai/new-page"])
        parser.assert_called_once_with("pippit", site["fallback"], set())


if __name__ == "__main__":
    unittest.main()
