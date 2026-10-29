"""Reads the "Download your data" export from TikTok (Settings > Account > Download your data).

Supports the JSON export (``user_data.json`` / ``user_data_tiktok.json``), the
TXT export (``Watch History.txt``, ``Like List.txt`` ...) and the .zip TikTok
sends you containing either. TikTok renames sections between versions, so
rather than hard-coding paths we walk the whole file and classify every list
of ``{Date, Link}`` records by the name of the section it sits in.
"""

from __future__ import annotations

import json
import re
import statistics
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

VIDEO_ID_RE = re.compile(r"/video/(\d+)")
DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d")

SESSION_GAP = 600.0  # seconds; a longer gap means you closed the app
MAX_WATCH_ESTIMATE = 300.0


@dataclass
class Record:
    ts: float
    link: str = ""
    text: str = ""

    @property
    def video_id(self) -> str | None:
        m = VIDEO_ID_RE.search(self.link)
        return m.group(1) if m else None


@dataclass
class ExportData:
    watched: list[Record] = field(default_factory=list)
    liked: list[Record] = field(default_factory=list)
    favorited: list[Record] = field(default_factory=list)
    shared: list[Record] = field(default_factory=list)
    searches: list[Record] = field(default_factory=list)
    following: list[Record] = field(default_factory=list)
    hashtags: list[Record] = field(default_factory=list)
    comments: list[Record] = field(default_factory=list)

    def is_empty(self) -> bool:
        return not any([self.watched, self.liked, self.favorited, self.searches, self.following])

    def summary(self) -> dict[str, int]:
        return {
            "watched": len(self.watched),
            "liked": len(self.liked),
            "favorited": len(self.favorited),
            "shared": len(self.shared),
            "searches": len(self.searches),
            "following": len(self.following),
            "favorite_hashtags": len(self.hashtags),
            "comments": len(self.comments),
        }

    # -- derived signals ---------------------------------------------------
    def estimated_watch_seconds(self) -> dict[str, float]:
        """Estimate time spent on each watched video from the gap to the next one.

        The export only stores when each video started. Within one session the
        next video starts right after you leave the previous one, so the gap is
        a good proxy for watch time (rewatches add up).
        """
        ordered = sorted((r for r in self.watched if r.video_id), key=lambda r: r.ts)
        result: dict[str, float] = {}
        for cur, nxt in zip(ordered, ordered[1:]):
            gap = nxt.ts - cur.ts
            if 0 <= gap <= SESSION_GAP:
                vid = cur.video_id
                assert vid is not None
                result[vid] = result.get(vid, 0.0) + min(gap, MAX_WATCH_ESTIMATE)
        return result

    def positive_ids(self) -> set[str]:
        ids = set()
        for rec in [*self.liked, *self.favorited, *self.shared]:
            if rec.video_id:
                ids.add(rec.video_id)
        return ids

    def watch_stats(self) -> dict[str, float]:
        est = list(self.estimated_watch_seconds().values())
        if not est:
            return {}
        skips = sum(1 for s in est if s <= 3)
        return {
            "videos_with_estimate": len(est),
            "median_watch_seconds": statistics.median(est),
            "skip_rate": skips / len(est),
        }

    def seed_interests(self) -> dict[str, float]:
        """Interest tokens we can read straight from the export, no network needed."""
        interests: dict[str, float] = {}

        def bump(token: str, amount: float) -> None:
            token = token.strip().lower()
            if len(token) < 2:
                return
            interests[token] = min(3.0, interests.get(token, 0.0) + amount)

        for rec in self.hashtags:
            name = rec.text or rec.link.rstrip("/").rsplit("/", 1)[-1]
            if name:
                bump("#" + name.lstrip("#"), 1.5)
        for rec in self.following:
            if rec.text:
                bump("@" + rec.text.lstrip("@"), 1.0)
        for rec in self.searches:
            for word in re.findall(r"#?[^\W\d_][\w]{2,}", rec.text.lower()):
                bump(word if word.startswith("#") else word, 0.4)
                if not word.startswith("#"):
                    bump("#" + word, 0.3)  # a search for "cats" usually means #cats too
        return interests


# -- parsing -----------------------------------------------------------------

def _parse_date(value: Any) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip().replace(" UTC", "")
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).timestamp()
        except ValueError:
            continue
    return 0.0


def _get(d: dict, *names: str) -> Any:
    # keys differ between exports: "SearchTerm" in JSON, "Search Term" in TXT
    norm = {k.lower().replace(" ", "").replace("_", ""): v for k, v in d.items()}
    for n in names:
        key = n.lower().replace(" ", "")
        if key in norm:
            return norm[key]
    return None


def _classify(path: str) -> str | None:
    # only look at the section name and the list key, e.g. "Like List/ItemFavoriteList";
    # parents like "Likes and Favorites" would otherwise match several kinds
    p = "/".join(path.lower().split("/")[-2:])
    if "live" in p:
        return None
    if "hashtag" in p:
        return "hashtags"
    if "favorite video" in p or "favoritevideolist" in p:
        return "favorited"
    if "like" in p:  # "Like List/ItemFavoriteList" is TikTok's name for likes
        return "liked"
    if "browsing" in p or "watch" in p:
        return "watched"
    if "share" in p:
        return "shared"
    if "search" in p:
        return "searches"
    if "following" in p:
        return "following"
    if "comment" in p:
        return "comments"
    return None


def _records_from_list(items: list, kind: str) -> list[Record]:
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        ts = _parse_date(_get(item, "Date", "date", "Time"))
        link = _get(item, "Link", "VideoLink", "Url", "link") or ""
        text = _get(item, "SearchTerm", "UserName", "Username", "HashtagName", "Comment", "SharedContent") or ""
        if kind == "hashtags" and not text:
            text = _get(item, "HashtagLink", "Hashtag") or ""
        if link or text:
            out.append(Record(ts=ts, link=str(link), text=str(text)))
    return out


def _walk(node: Any, path: str, data: ExportData) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            _walk(value, f"{path}/{key}", data)
    elif isinstance(node, list) and node and isinstance(node[0], dict):
        kind = _classify(path)
        if kind:
            getattr(data, kind).extend(_records_from_list(node, kind))


def parse_json(text: str) -> ExportData:
    data = ExportData()
    _walk(json.loads(text), "", data)
    return data


_TXT_FILES = {
    "watch history": "watched",
    "browsing history": "watched",
    "like list": "liked",
    "liked": "liked",
    "favorite videos": "favorited",
    "favorite hashtags": "hashtags",
    "share history": "shared",
    "search history": "searches",
    "searches": "searches",
    "following": "following",
    "comments": "comments",
}


def parse_txt(name: str, text: str, data: ExportData | None = None) -> ExportData:
    """TXT exports are blocks of ``Key: value`` lines separated by blank lines."""
    data = data or ExportData()
    stem = Path(name).stem.lower()
    kind = next((k for key, k in _TXT_FILES.items() if key in stem), None)
    if kind is None:
        return data
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                fields[key.strip()] = value.strip()
        if fields:
            getattr(data, kind).extend(_records_from_list([fields], kind))
    return data


def _merge(into: ExportData, other: ExportData) -> None:
    for name in into.__dataclass_fields__:
        getattr(into, name).extend(getattr(other, name))


def _parse_named(name: str, raw: bytes, data: ExportData) -> None:
    text = raw.decode("utf-8-sig", errors="replace")
    if name.lower().endswith(".json"):
        try:
            _merge(data, parse_json(text))
        except ValueError:
            pass
    elif name.lower().endswith(".txt"):
        parse_txt(name, text, data)


def load_export(path: str | Path) -> ExportData:
    """Load a TikTok export from a .json, .txt, .zip file or an unzipped folder."""
    path = Path(path)
    data = ExportData()
    if path.is_dir():
        for f in sorted(path.rglob("*")):
            if f.suffix.lower() in (".json", ".txt"):
                _parse_named(f.name, f.read_bytes(), data)
    elif path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                if info.file_size > 500 * 1024 * 1024:
                    continue
                if info.filename.lower().endswith((".json", ".txt")):
                    _parse_named(Path(info.filename).name, zf.read(info), data)
    else:
        _parse_named(path.name, path.read_bytes(), data)
    for name in data.__dataclass_fields__:
        getattr(data, name).sort(key=lambda r: r.ts)
    return data


def video_url(video_id: str) -> str:
    return f"https://www.tiktok.com/@_/video/{video_id}"


def unique_ids(records: Iterable[Record]) -> list[str]:
    return list(dict.fromkeys(r.video_id for r in records if r.video_id))

