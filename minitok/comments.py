"""Exporting the downloaded comments. Comments are read-only: view and export, nothing else."""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from pathlib import Path

from .models import Comment, Video

EXPORT_FORMATS = {
    "json": "JSON (*.json)",
    "csv": "CSV spreadsheet (*.csv)",
    "txt": "Plain text (*.txt)",
}


def _date(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else ""


def render(video: Video, comments: list[Comment], fmt: str) -> str:
    if fmt == "json":
        return json.dumps(
            {
                "video": {"id": video.id, "url": video.url, "author": video.author,
                          "description": video.description},
                "comments": [c.to_dict() for c in comments],
            },
            indent=2,
            ensure_ascii=False,
        )
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["author", "text", "likes", "replies", "date", "comment_id", "video_url"])
        for c in comments:
            writer.writerow([c.author, c.text, c.likes, c.reply_count, _date(c.created), c.id, video.url])
        return buf.getvalue()
    if fmt == "txt":
        lines = [f"Comments on {video.url}", f"@{video.author}: {video.description}", ""]
        for c in comments:
            meta = f"{c.likes} likes" + (f", {_date(c.created)}" if c.created else "")
            lines.append(f"@{c.author} ({meta})")
            lines.append(f"  {c.text}")
            lines.append("")
        return "\n".join(lines)
    raise ValueError(f"Unknown export format: {fmt}")


def export(video: Video, comments: list[Comment], path: str | Path, fmt: str | None = None) -> Path:
    path = Path(path)
    fmt = fmt or path.suffix.lstrip(".").lower() or "json"
    if fmt not in EXPORT_FORMATS:
        fmt = "json"
    # utf-8-sig so Excel opens CSV accents/emoji correctly
    path.write_text(render(video, comments, fmt), encoding="utf-8-sig" if fmt == "csv" else "utf-8")
    return path
