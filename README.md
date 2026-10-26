# Competitor sitemap crawler

Tracks newly indexed, default-language URLs for Aireel, DomoAI, EaseMate, Kapwing, Viddo, Supermaker, and Mitte. It discovers sitemaps from each site's public `robots.txt` with a verified fallback, sends one Bark alert per changed site, and stores a separate gzip-compressed baseline per domain.

The initial scheduled run records baselines without alerting. Later runs only update a baseline after all required Bark pushes succeed, so delivery errors remain retryable. The workflow runs every six hours and uses the repository secrets `BARK_DEVICE_KEY` and optional `BARK_SERVER_URL`.

## MakeViral templates

`makeviral_sitemap_crawler.py` separately tracks new public `/templates/{slug}` pages from MakeViral's sitemap. Its baseline is `state/makeviral_urls.txt`; newly found templates get a metadata report and one Bark alert in the `makeviral-templates` group. A failed page fetch or Bark push leaves the baseline unchanged so the next run retries. The MakeViral workflow runs every 12 hours at 01:53 and 13:53 UTC (09:53 and 21:53 in Shanghai) using the same Bark secrets. Existing templates are baseline data and do not trigger an alert.

Use the workflow's `test_notification` input to verify delivery without touching the baseline.

```bash
python3 makeviral_sitemap_crawler.py --bootstrap  # first run only
python3 makeviral_sitemap_crawler.py
python3 -m unittest discover -s tests -p 'test_makeviral.py'
```

```bash
python3 -m unittest discover -s tests
python3 competitor_sitemap_crawler.py --site kapwing --bootstrap
python3 competitor_sitemap_crawler.py --site kapwing
```
