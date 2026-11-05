"""Builds the data for the in-browser preview at docs/app/.

    python scripts/build_web_preview.py

Renders the offline demo clips (needs ffmpeg) into docs/app/media/ and writes
docs/app/data.json with each clip's metadata and placeholder comments.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from minitok.demo import make_demo  # noqa: E402
from minitok.models import Video  # noqa: E402

APP = ROOT / "docs" / "app"


def main() -> None:
    media = APP / "media"
    shutil.rmtree(media, ignore_errors=True)
    media.mkdir(parents=True)
    videos = []
    with tempfile.TemporaryDirectory() as tmp:
        folder = make_demo(tmp, count=12)
        for clip in sorted(folder.glob("*.mp4")):
            info = json.loads(clip.with_name(clip.stem + ".info.json").read_text(encoding="utf-8"))
            comments = json.loads(clip.with_name(clip.stem + ".comments.json").read_text(encoding="utf-8"))
            v = Video.from_ytdlp(info)
            shutil.copy(clip, media / clip.name)
            videos.append({
                "id": v.id, "src": f"media/{clip.name}", "author": v.author, "description": v.description,
                "hashtags": v.hashtags, "music": v.music, "duration": v.duration, "views": v.views,
                "likes": v.likes, "comment_count": v.comment_count, "shares": v.shares, "created": v.created,
                "comments": [
                    {"id": str(c["id"]), "author": c["author"], "text": c["text"],
                     "likes": c.get("like_count", 0), "created": c.get("timestamp", 0), "reply_count": 0}
                    for c in comments
                ],
            })
    (APP / "data.json").write_text(json.dumps({"videos": videos}, ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(videos)} clips to {media}")


if __name__ == "__main__":
    main()
