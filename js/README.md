# MiniTok.js

A TikTok-style For You feed you can drop into any web page or AI-built app, like you would Leaflet.
Real TikTok videos play through **TikTok's official Embed Player**; **MiniTok's algorithm** decides the
order (presets, weight sliders, a custom formula, learning from what you watch). One file, no dependencies.

[Live demo](https://vendemiaraul-cyber.github.io/python-disconnected/app/)

## Use it

```html
<div id="feed" style="width:400px;height:720px"></div>
<script src="https://cdn.jsdelivr.net/gh/vendemiaraul-cyber/python-disconnected@032b28bca70cfcb62cf34c59db35c8c2089b157b/js/dist/minitok.js"></script>
<script>
  const feed = MiniTok.mount("#feed");
</script>
```

That link is pinned to an exact version, so it never changes under you. (When the repo gets a `v1.0.0`
release, `python-disconnected@1/js/dist/minitok.js` will also work and follow 1.x updates.)

As an ES module:

```js
import MiniTok from "https://cdn.jsdelivr.net/gh/vendemiaraul-cyber/python-disconnected@032b28bca70cfcb62cf34c59db35c8c2089b157b/js/dist/minitok.mjs";
MiniTok.mount(document.getElementById("feed"), { algorithm: { preset: "discovery" } });
```

The widget fills its container (give it a height), lives in a Shadow DOM so it won't clash with your
styles, and saves the viewer's settings and watch history in their browser.

## `MiniTok.mount(element, options)`

| option | default | |
|---|---|---|
| `videos` | 5 starter videos | TikTok links, ids, or `{id, author, description}` objects |
| `algorithm` | `{preset: "for_you"}` | `preset`, `weights`, `formula`, `exploration`, `diversity`, `interests`, `blocked`, `preferredDuration`, `learn` |
| `message` | shown | `{show, text}`: the note shown on every video (default: a message to TikTok asking for a better embed) |
| `comments` | none | `async (video) => [{author, text, likes, created}]`, or `MiniTok.commentsFrom("http://127.0.0.1:8787")` |
| `settings` | `true` | show the ⚙ algorithm settings |
| `storage` | `"minitok"` | localStorage namespace, or `false` to keep nothing |
| `oembed` | `true` | fill captions/creators from TikTok's official oEmbed |
| `onChange(video, info)` | | a new video is on screen (`info.score`, `info.features`, `info.explored`) |
| `onWatch(impression)` | | the viewer left a video (`watched` seconds, `loops`) |
| `onError({video, code})` | | TikTok couldn't play a video (it's skipped) |
| `onSettings(config)` | | the viewer saved new settings |

It returns a controller: `next()`, `previous()`, `play()`, `pause()`, `mute()`, `unmute()`,
`addVideo(link)`, `setAlgorithm({...})`, `setMessage({show, text})`, `openSettings(tab)`,
`openComments()`, `destroy()`, and read-only `current`, `videos`, `history`, `algorithm`.

## The algorithm on its own

```js
const ranked = MiniTok.rank(
  ["https://www.tiktok.com/@hankgreen1/video/7047596209028074758", "6718335390845095173"],
  { preset: "niche", interests: { "@hankgreen1": 3, "#cats": 1 } }
);
ranked[0].video.author;   // "hankgreen1"
ranked[0].features;       // interest, popularity, like_rate, ... (each 0..1)

new MiniTok.Formula('3*interest + (2 if has("#cats") else 0)');   // safe: no code can run
MiniTok.learn(history, seeds, stats);       // fit weights + interests from watch history
MiniTok.parseExport("user_data_tiktok.json", text);   // read a TikTok "Download your data" file
MiniTok.exportComments(video, comments, "csv");       // "json" | "csv" | "txt"
```

Presets: `for_you`, `popular`, `fresh`, `niche`, `discovery`, `random`, `custom`, `formula`, `learned`.

## Comments

Browsers can't fetch TikTok comments (TikTok doesn't allow it), so the comments button appears only
when you give a `comments` source. The MiniTok desktop app includes one:

```bash
pip install git+https://github.com/vendemiaraul-cyber/python-disconnected@claude/minitok-app
minitok serve            # http://127.0.0.1:8787 - /comments?id=..., /rank?videos=...
```

```js
MiniTok.mount("#feed", { comments: MiniTok.commentsFrom("http://127.0.0.1:8787") });
```

It returns up to 30 comments per video (read-only), which the widget lets you export as JSON, CSV or
text. Run it on a home connection; TikTok often refuses data-centre servers.

## Limits, honestly

- TikTok's player always shows TikTok's own creator name and heart/comment/share buttons. MiniTok keeps
  them visible, as TikTok's embed rules require.
- TikTok has no official feed for apps, so the algorithm ranks the videos you give it (or that viewers
  add by link in ⚙). Videos whose creators turned off embedding are skipped.
- The player shares no view/like counts, so popularity signals are weak; interests and watch time drive it.
- Browsers start videos muted; the first tap turns sound on.

## Develop

```bash
node build.mjs           # src/ -> dist/minitok.js, dist/minitok.mjs (and the demo's copy)
npm test                 # unit tests (node:test, no dependencies)
```

MIT License.
