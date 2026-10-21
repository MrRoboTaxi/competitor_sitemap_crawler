import unittest

from competitor_sitemap_crawler import normalize_target


class NormalizeTargetTest(unittest.TestCase):
    def test_removes_query_and_fragment(self):
        self.assertEqual(normalize_target("https://viddo.ai/blog/new/?source=x#top", "viddo.ai"), "https://viddo.ai/blog/new")

    def test_excludes_locale_and_other_host(self):
        self.assertIsNone(normalize_target("https://www.aireel.net/ja/feature", "www.aireel.net"))
        self.assertIsNone(normalize_target("https://example.com/page", "www.aireel.net"))


if __name__ == "__main__":
    unittest.main()
