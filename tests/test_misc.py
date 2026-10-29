import csv
import io
import json

from minitok.comments import export, render
from minitok.config import COMMENT_LIMIT, AppConfig
from minitok.downloader import Downloader
from minitok.models import Comment, Video, human_count
from minitok.sources import LocalFolderSource, normalize_spec
from minitok.storage import Cache

VIDEO = Video(id="1", url="https://x/1", author="me", description="hello")
COMMENTS = [Comment(id="a", author="bob", text='hi, "there" 👋', likes=3), Comment(id="b", author="amy", text="yo")]


def test_export_formats(tmp_path):
    data = json.loads(render(VIDEO, COMMENTS, "json"))
    assert data["comments"][0]["text"] == 'hi, "there" 👋'
    rows = list(csv.reader(io.StringIO(render(VIDEO, COMMENTS, "csv"))))
    assert rows[1][:2] == ["bob", 'hi, "there" 👋']
    assert "@amy" in render(VIDEO, COMMENTS, "txt")
    out = export(VIDEO, COMMENTS, tmp_path / "c.csv")
    assert out.read_text(encoding="utf-8-sig").startswith("author,")


def test_spec_normalisation():
    assert normalize_spec("@bob") == "https://www.tiktok.com/@bob"
    assert normalize_spec("#cats") == "https://www.tiktok.com/tag/cats"
    assert normalize_spec("123") == "https://www.tiktok.com/@_/video/123"


def test_local_folder_source_and_comment_cap(tmp_path):
    (tmp_path / "clip.mp4").write_bytes(b"x")
    (tmp_path / "clip.info.json").write_text(json.dumps({"id": "z", "uploader": "amy", "description": "#cats",
                                                         "view_count": 5, "duration": 7}))
    (tmp_path / "clip.comments.json").write_text(json.dumps([{"id": i, "text": "t"} for i in range(45)]))
    (tmp_path / "notes.txt").write_text("ignored")
    src = LocalFolderSource(tmp_path)
    [v] = src.fetch(10)
    assert v.author == "amy" and v.hashtags == ["cats"] and v.local_path.endswith("clip.mp4")
    assert src.fetch(10) == []
    cache = Cache(tmp_path / "cache")
    dl = Downloader(cache, AppConfig())
    assert dl.download(v) == tmp_path / "clip.mp4"
    assert len(dl.comments(v)) == COMMENT_LIMIT
    assert cache.comments_file(v.id).exists()


def test_config_roundtrip():
    cfg = AppConfig()
    cfg.algorithm.apply_preset("discovery")
    cfg.algorithm.interests["#cats"] = 1.5
    cfg.sources = ["@bob"]
    cfg.save()
    loaded = AppConfig.load()
    assert loaded.algorithm.preset == "discovery"
    assert loaded.algorithm.interests == {"#cats": 1.5}
    assert loaded.sources == ["@bob"]


def test_human_count():
    assert human_count(999) == "999"
    assert human_count(1500) == "1.5K"
    assert human_count(2_000_000) == "2M"


def test_video_from_ytdlp_tiktok_like_info():
    v = Video.from_ytdlp({"id": "7", "webpage_url": "https://www.tiktok.com/@a/video/7",
                          "description": "omg #Cats #fyp", "uploader": "a", "view_count": 10,
                          "track": "song", "artist": "band", "timestamp": 1700000000})
    assert v.hashtags == ["cats", "fyp"] and v.music == "song - band" and v.created == 1700000000
    assert "#cats" in v.tokens() and "@a" in v.tokens() and "♪song - band" in v.tokens()


def test_tiktok_count_matches_app_format():
    from minitok.models import tiktok_count

    assert [tiktok_count(n) for n in (441, 4489, 18490, 1_250_000)] == ["441", "4489", "18.490", "1,2M"]
