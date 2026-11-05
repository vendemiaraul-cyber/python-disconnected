# MiniTok

A mini TikTok for your desktop: a phone-sized For You feed that downloads a few
videos at a time, lets you **view and export up to 30 comments** per video, and
gives you full control over the **algorithm** that picks what comes next,
including fitting it to **your own data**.

```
┌──────────────────────────┐
│   Following  For You   ⚙ │  ⚙ = algorithm & settings
│                          │
│          video           │
│                     💬   │  comments (view only, max 30)
│                    1423  │
│                     ➦    │  export comments (JSON / CSV / TXT)
│                    2808  │
│ @creator                 │
│ caption #tags            │
│ ♪ sound                  │
└──────────────────────────┘
```

Project page: https://vendemiaraul-cyber.github.io/python-disconnected/ (published from the `gh-pages` branch;
source in `docs/`). **Try it without installing:** the page's browser preview (`docs/app/`) runs the same feed,
comments, export and algorithm on the demo clips. Rebuild its clips with `python scripts/build_web_preview.py`.

## Install & run

```bash
pip install -e .            # PySide6, yt-dlp, Scrapy
minitok demo                # offline demo: makes 12 clips (needs ffmpeg) and opens them
minitok --source @nasa --source "#cooking"   # real TikTok creators / hashtags
minitok --folder ~/Videos/clips              # your own video files
```

Linux needs the usual Qt system libraries (`libegl1 libgl1 libxkbcommon0 libfontconfig1`).

**Controls:** scroll / drag up / ↓ / J → next · ↑ / K → previous · click / Space → pause ·
C → comments · E → export · S → settings · M → mute · I → "why am I seeing this?".

## What it does

| Feature | How |
|---|---|
| TikTok-style UI | Full-bleed video, For You header, right-hand comment + export buttons with counts, caption and ♪ sound bottom-left, thin progress bar, videos loop like the app. |
| Downloads some videos | Only the previous, current and next *N* videos (default 2) are on disk. Everything else is deleted as you scroll; the cache is wiped on exit. |
| 30 comments | Downloaded when a video comes on screen, deleted the moment you swipe away. Read-only: view and export, nothing else. |
| Export | The export button (or the sheet's Export button) saves the comments as JSON, CSV or TXT. |
| Settings button on the FYP | ⚙ opens the algorithm editor (below). |
| Your own data | Load your TikTok data export and/or use your MiniTok history to fit your personal algorithm. |

### Comments: Scrapy
TikTok comments are fetched with a **Scrapy** spider (`minitok/scrapy_comments.py`)
that pages through TikTok's public web comment list and stops at 30. Scrapy's
Twisted reactor can only run once per process, so each video's crawl runs in a
short child process. If Scrapy isn't installed or the crawl fails, MiniTok falls
back to fetching the same endpoint through yt-dlp's networking. Non-TikTok
sources use yt-dlp's own comment extractors; local folders read
`name.comments.json`.

## The algorithm

Every candidate video gets nine 0..1 signals: `interest` (matches your
hashtags, creators, sounds and words), `popularity`, `like_rate`,
`comment_rate`, `share_rate`, `freshness`, `duration_fit`, `novelty`, `random`.

**Types of algorithm** (⚙ › Algorithm):

* **For You (TikTok-style)**: interest first, engagement quality, freshness, 10% exploration
* **Most Popular**, **Fresh / Newest**, **Niche / Interests only**, **Discovery / Small creators**, **Random**
* **Custom weights**: a slider per signal (moving any slider switches to this)
* **Custom formula**: type your own, e.g.
  `3*interest + like_rate + (2 if has("#cats") else 0) - 0.5*popularity`
  (sandboxed: arithmetic, comparisons, `if/else`, `log/sqrt/min/max/clamp`, `has()`, `contains()`)
* **Learned from my data**: weights fitted by "Find my algorithm"

You can also set exploration, creator variety, preferred length, edit the interest
profile, block words / #tags / @creators, preview the ranking of waiting videos,
and import/export an algorithm as JSON to share it.

While you watch, MiniTok learns: finishing or looping a video raises its
hashtags/creator/sound, skipping lowers them, and opening the comments counts as
interest. Press **I** to see the score breakdown for the current video.

### Find my algorithm (your own data)
1. In TikTok: *Settings › Account › Download your data* (JSON).
2. ⚙ › *Find my algorithm* › *Load export file…* (zip, json, txt or folder).
3. MiniTok reads watch history, likes, favourites, shares, searches, follows and
   favourite hashtags. It estimates how long you watched each video from the gaps
   between timestamps, looks up a sample of those videos (views, likes, tags,
   length), and fits a logistic regression plus a log-odds interest profile.
4. Read the report (what drives your feed, top interests, preferred length,
   suggested exploration) and press *Use this as my algorithm*.

Your MiniTok watch history is used the same way and needs no internet. Command
line: `minitok learn user_data_tiktok.json --lookup 60`.

> TikTok's real recommender is private, so no app can reproduce it exactly.
> What this recovers is **your** ranking: the simplest model that best explains
> what you actually finished, rewatched and liked.

## Sources
⚙ › Sources, one per line: `@creator`, `#hashtag`, a TikTok link, or any URL yt-dlp
supports (e.g. YouTube Shorts). "Add the creators I follow" fills it from your
export. If TikTok refuses anonymous requests, pick a browser under ⚙ › Storage ›
*Use cookies from*. Respect creators' rights and TikTok's terms; downloads are
for personal viewing and are deleted as you scroll.

## Data
Settings, learned interests and watch history live in `~/.minitok`
(`MINITOK_HOME` to change). Nothing is uploaded.

## Development
```bash
pip install -e ".[dev]" && pytest
```
`minitok/feed.py` (feed, prefetch, deletion), `algorithm/` (features, presets,
formula, ranker, learner, TikTok export parser), `scrapy_comments.py`,
`downloader.py`, `ui/` (Qt).
