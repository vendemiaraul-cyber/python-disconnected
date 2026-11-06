# Video Watcher

Search once and see videos from several websites side by side, then watch them without leaving the page.

| Source | Site shown on each result | Ads |
|---|---|---|
| YouTube | youtube.com | Yes: played in YouTube's official embed player, so YouTube's ads run as normal |
| Dailymotion | dailymotion.com | Yes: played in Dailymotion's official player, ads run as normal |
| PeerTube | the instance hosting the video (e.g. `tilvids.com`) | No: federated and ad-free |
| Internet Archive | archive.org | No: non-profit and ad-free |

No API keys are needed.

## How it works

- **Parallel search.** A query is sent to every selected site at once. Results are interleaved round-robin so no single site takes over the top of the list. A site that errors or takes too long (15 s deadline) is reported as "unavailable" and the others still show.
- **Where it came from.** Every card shows a coloured source badge plus the actual website domain, linked to the original watch page.
- **Ads where the site needs them.** Videos always play through each site's **official embed player** (`youtube.com/embed/…`, `geo.dailymotion.com/player.html…`, PeerTube `/videos/embed/…`, `archive.org/embed/…`). Nothing is downloaded, re-streamed or ad-stripped, so ad-supported sites (marked **ADS**) show their ads exactly as they intend, and the creator still gets paid. The player shows a note when a video comes from an ad-supported site.
- **Filters.** Use the source chips at the top to choose which sites to search.
- Results are cached in memory for 5 minutes, but only when every source answered.

## Run it

```bash
pip install -r requirements.txt
python run.py            # http://127.0.0.1:5000
```

Environment variables: `HOST`, `PORT`, `FLASK_DEBUG=1`.

## JSON API

`GET /api/search?q=<query>&limit=<1-25>&sources=youtube,peertube`

```json
{
  "query": "lofi",
  "results": [
    {"id": "…", "title": "…", "url": "https://www.youtube.com/watch?v=…",
     "embed_url": "https://www.youtube.com/embed/…", "source": "youtube",
     "source_name": "YouTube", "source_domain": "youtube.com", "ad_supported": true,
     "thumbnail": "…", "duration": 685, "channel": "…", "views": 1006585, "published": null}
  ],
  "sources": {"youtube": {"ok": true, "count": 8, "error": null}}
}
```

`GET /api/sources` lists the available sources.

## Adding a source

Subclass `videowatcher.providers.base.Provider` and set `key`, `name`, `domain` and `ad_supported`. Implement `search(query, limit)` so it returns `Video` objects whose `embed_url` is that site's official player, then register the class in `videowatcher/providers/__init__.py`.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```
