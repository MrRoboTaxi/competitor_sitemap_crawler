# Competitor sitemap crawler

Tracks newly indexed, default-language URLs for Aireel, DomoAI, EaseMate, Kapwing, Viddo, Supermaker, and Pippit. It discovers sitemaps from each site's public `robots.txt` with a verified fallback, sends one Bark alert per changed site, and stores a separate baseline per domain.

The initial scheduled run records baselines without alerting. Later runs only update a baseline after all required Bark pushes succeed, so delivery errors remain retryable. The workflow runs every six hours and uses the repository secrets `BARK_DEVICE_KEY` and optional `BARK_SERVER_URL`.

```bash
python3 -m unittest discover -s tests
python3 competitor_sitemap_crawler.py --site kapwing --bootstrap
python3 competitor_sitemap_crawler.py --site kapwing
```
