"""Generates an offline demo folder: short vertical clips with fake metadata and comments.

    python -m minitok demo ./demo_videos

Needs the ``ffmpeg`` command. Each clip gets a ``.info.json`` (views, likes,
hashtags, creator...) and a ``.comments.json`` with 40 comments, so you can
try the algorithm settings and the 30-comment limit without any network.
"""

from __future__ import annotations

import json
import random
import shutil
import subprocess
import time
from pathlib import Path

TOPICS = {
    "cats": ("#cats #pets #cute", ["catlady", "whiskers.daily", "meowmeow"], "0xff8a65"),
    "cooking": ("#cooking #recipe #food", ["chef.nina", "quickbites", "pastaking"], "0xffd54f"),
    "football": ("#football #sports #goals", ["goal.clips", "ultras_tv"], "0x66bb6a"),
    "coding": ("#coding #python #programming", ["dev.dan", "pythontips"], "0x42a5f5"),
    "dance": ("#dance #trend #fyp", ["moves.by.mia", "dancecrew"], "0xab47bc"),
    "travel": ("#travel #japan #wanderlust", ["nomad.sam", "tokyo.walks"], "0x26c6da"),
}
SOUNDS = ["original sound", "Espresso - Sabrina Carpenter", "APT. - ROSÉ", "lofi beat 3"]
WORDS = ["wow", "this is so good", "no way 😂", "need part 2", "who else is here in 2026",
         "the ending!!", "saving this", "I tried it and it worked", "underrated", "🔥🔥🔥",
         "algorithm brought me here", "first", "how do you do that", "obsessed", "lol"]


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
        tags, authors, color = TOPICS[topic]
        duration = rng.choice([6, 8, 10, 15])
        stem = f"{i + 1:02d}_{topic}"
        out = folder / f"{stem}.mp4"
        if not out.exists():
            freq = 220 + 110 * (i % 5)
            cmd = [
                ffmpeg, "-y", "-loglevel", "error",
                "-f", "lavfi", "-i", f"color=c={color}:s=360x640:d={duration}:r=24",
                "-f", "lavfi", "-i", f"testsrc2=s=200x200:d={duration}:r=24",
                "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={duration}",
                "-filter_complex", "[0][1]overlay=(W-w)/2:(H-h)/2-60[v]",
                "-map", "[v]", "-map", "2:a",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
                "-c:a", "aac", "-shortest", str(out),
            ]
            subprocess.run(cmd, check=True)
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
        comments = [
            {
                "id": f"{stem}-c{j}",
                "author": f"user{rng.randint(100, 9999)}",
                "text": rng.choice(WORDS),
                "like_count": int(rng.expovariate(1 / 200)),
                "timestamp": int(now - rng.uniform(0, 24 * 10) * 3600),
            }
            for j in range(40)
        ]
        comments.sort(key=lambda c: c["like_count"], reverse=True)
        (folder / f"{stem}.comments.json").write_text(json.dumps(comments, ensure_ascii=False), encoding="utf-8")
    return folder
