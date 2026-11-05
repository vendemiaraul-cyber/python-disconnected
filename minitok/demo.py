"""Generates an offline demo folder: short vertical clips with fake metadata and comments.

    python -m minitok demo ./demo_videos

Needs the ``ffmpeg`` command. Each clip gets a ``.info.json`` (views, likes,
hashtags, creator...) and a ``.comments.json`` with 34 placeholder comments, so you can
try the algorithm settings and the 30-comment limit without any network.
"""

from __future__ import annotations

import json
import random
import shutil
import subprocess
import time
from pathlib import Path

# topic -> (hashtags, creators, gradient colours, topic-specific demo comments)
TOPICS = {
    "cats": ("#cats #pets #cute", ["catlady", "whiskers.daily", "meowmeow"], ("0xff6a3d", "0x8e2de2"),
             ["my cat does the exact same thing", "the little paws 😭", "he knows he's cute",
              "cats are just liquid", "the side eye at the end", "mine would never sit still like that"]),
    "cooking": ("#cooking #recipe #food", ["chef.nina", "quickbites", "pastaking"], ("0xf7b733", "0xfc4a1a"),
                ["recipe please!!", "making this tonight", "how long in the oven?", "the crunch 🤤",
                 "you can swap butter for olive oil", "my nonna would approve"]),
    "football": ("#football #sports #goals", ["goal.clips", "ultras_tv"], ("0x11998e", "0x38ef7d"),
                 ["what a strike", "keeper had no chance", "best goal of the season",
                  "that first touch though", "commentator lost it 😂", "replay it 10 times"]),
    "coding": ("#coding #python #programming", ["dev.dan", "pythontips"], ("0x2193b0", "0x6dd5ed"),
               ["finally someone explains it simply", "list comprehensions are underrated",
                "this saved my homework", "works on my machine", "can you do one on async?",
                "tabs vs spaces debate incoming"]),
    "dance": ("#dance #trend #fyp", ["moves.by.mia", "dancecrew"], ("0xee0979", "0xff6a00"),
              ["the timing is perfect", "learning this today", "tutorial please", "the energy!!",
               "my legs could never", "this trend never gets old"]),
    "travel": ("#travel #japan #wanderlust", ["nomad.sam", "tokyo.walks"], ("0x4568dc", "0xb06ab3"),
               ["adding this to my list", "which neighbourhood is this?", "best time of year to go?",
                "the view 😍", "I was there last spring", "how much was the trip?"]),
}
SOUNDS = ["original sound", "Espresso - Sabrina Carpenter", "APT. - ROSÉ", "lofi beat 3"]
WORDS = ["wow", "this is so good", "no way 😂", "need part 2", "who else is here in 2026",
         "the ending!!", "saving this", "underrated", "🔥🔥🔥", "algorithm brought me here",
         "first", "how do you do that", "obsessed", "lol", "came back to watch again",
         "why is this so satisfying", "sending this to my sister", "the quality 👌",
         "commenting so it shows up again", "this deserves more views", "okay but the music",
         "I didn't expect that", "watching this at 3am", "the way I gasped", "more of this please",
         "this is my whole personality now", "tell me why I smiled", "best thing on my fyp today"]
NAME_PARTS = (["luca", "sofia", "marco", "giulia", "alex", "emma", "leo", "nina", "sam", "mia", "tom", "zoe"],
              ["", ".", "_", "x"],
              ["", "99", "official", "daily", "2007", "vibes", "art", "_it", "fan"])
COMMENTS_PER_VIDEO = 34  # a few more than the 30 the app downloads
FONT_CANDIDATES = [
    "/usr/share/fonts/opentype/inter/Inter-ExtraBold.otf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]


def _font() -> str | None:
    return next((f for f in FONT_CANDIDATES if Path(f).exists()), None)


def _render_clip(ffmpeg: str, out: Path, topic: str, colors: tuple[str, str], index: int, duration: int) -> None:
    """A gradient that drifts, the topic in big letters, and a soft two-note tone."""
    c0, c1 = colors
    video = (f"gradients=s=360x640:c0={c0}:c1={c1}:x0=0:y0=0:x1=360:y1=640:speed=0.015"
             f":d={duration}:r=24")
    font = _font()
    vf = []
    if font:
        font = font.replace(":", r"\:")
        vf.append(f"drawtext=fontfile='{font}':text='{topic}':fontsize=72:fontcolor=white"
                  f":x=(w-text_w)/2:y=(h-text_h)/2-40+10*sin(t*2)")
        vf.append(f"drawtext=fontfile='{font}':text='demo clip {index}':fontsize=22:fontcolor=white@0.8"
                  f":x=(w-text_w)/2:y=h/2+40")
    f = 220 + 55 * (index % 6)
    audio = (f"aevalsrc='0.06*sin(2*PI*{f}*t)+0.04*sin(2*PI*{f * 1.5:.1f}*t)':s=44100:d={duration},"
             f"afade=t=in:d=0.4,afade=t=out:st={duration - 0.6}:d=0.6")
    cmd = [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi", "-i", video, "-f", "lavfi", "-i", audio]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd += ["-c:v", "libx264", "-crf", "30", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart", "-shortest", str(out)]
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError:
        # older ffmpeg without the gradients filter: plain colour background
        cmd[cmd.index(video)] = f"color=c={c0}:s=360x640:d={duration}:r=24"
        subprocess.run(cmd, check=True)


def make_demo(folder: str | Path, count: int = 12, seed: int = 3) -> Path:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("The demo needs ffmpeg installed (https://ffmpeg.org)")
    rng = random.Random(seed)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    topics = list(TOPICS)
    now = time.time()
    for i in range(count):
        topic = topics[i % len(topics)]
        tags, authors, colors, topic_comments = TOPICS[topic]
        duration = rng.choice([6, 8, 10, 15])
        stem = f"{i + 1:02d}_{topic}"
        out = folder / f"{stem}.mp4"
        if not out.exists():
            _render_clip(ffmpeg, out, topic, colors, i + 1, duration)
        views = int(10 ** rng.uniform(2.5, 7.3))
        info = {
            "id": stem,
            "title": f"{topic} clip {i + 1}",
            "description": f"Demo {topic} video number {i + 1} {tags}",
            "uploader": rng.choice(authors),
            "duration": duration,
            "view_count": views,
            "like_count": int(views * rng.uniform(0.02, 0.18)),
            "comment_count": int(views * rng.uniform(0.001, 0.01)),
            "repost_count": int(views * rng.uniform(0.0005, 0.02)),
            "timestamp": int(now - rng.uniform(1, 24 * 20) * 3600),
            "track": rng.choice(SOUNDS),
        }
        (folder / f"{stem}.info.json").write_text(json.dumps(info, indent=1, ensure_ascii=False), encoding="utf-8")
        texts = rng.sample(topic_comments + WORDS, COMMENTS_PER_VIDEO)  # no repeats within a video
        comments = [
            {
                "id": f"{stem}-c{j}",
                "author": "".join(rng.choice(part) for part in NAME_PARTS) or "user",
                "text": texts[j],
                "like_count": int(rng.expovariate(1 / 200)),
                "timestamp": int(now - rng.uniform(0, 24 * 10) * 3600),
            }
            for j in range(COMMENTS_PER_VIDEO)
        ]
        comments.sort(key=lambda c: c["like_count"], reverse=True)
        (folder / f"{stem}.comments.json").write_text(json.dumps(comments, ensure_ascii=False), encoding="utf-8")
    return folder
