import json
import zipfile

import pytest

from minitok.algorithm.tiktok_export import load_export

NEW_FORMAT = {
    "Your Activity": {
        "Watch History": {"VideoList": [
            {"Date": "2026-01-01 10:00:00", "Link": "https://www.tiktokv.com/share/video/111/"},
            {"Date": "2026-01-01 10:00:02", "Link": "https://www.tiktokv.com/share/video/222/"},
            {"Date": "2026-01-01 10:00:47", "Link": "https://www.tiktokv.com/share/video/333/"},
            {"Date": "2026-01-01 15:00:00", "Link": "https://www.tiktokv.com/share/video/444/"},
        ]},
        "Searches": {"SearchList": [{"Date": "2026-01-01 09:00:00", "SearchTerm": "cat videos"}]},
        "Following": {"Following": [{"Date": "2025-12-01 09:00:00", "UserName": "catlady"}]},
        "Share History": {"ShareHistoryList": [{"Date": "2026-01-01 10:01:00", "Link": "https://www.tiktokv.com/share/video/333/"}]},
        "Watch Live History": {"WatchLiveMap": {}},
    },
    "Likes and Favorites": {
        "Like List": {"ItemFavoriteList": [{"date": "2026-01-01 10:00:30", "link": "https://www.tiktokv.com/share/video/222/"}]},
        "Favorite Videos": {"FavoriteVideoList": [{"Date": "2026-01-01 10:00:30", "Link": "https://www.tiktokv.com/share/video/555/"}]},
        "Favorite Hashtags": {"FavoriteHashtagList": [{"Date": "2026-01-01 10:00:30", "Link": "https://www.tiktok.com/tag/kittens"}]},
        "Favorite Sounds": {"FavoriteSoundList": [{"Date": "2026-01-01 10:00:30", "Link": "x"}]},
    },
    "Comment": {"Comments": {"CommentsList": [{"Date": "2026-01-01 10:00:30", "Comment": "so cute"}]}},
}

OLD_FORMAT = {
    "Activity": {
        "Video Browsing History": {"VideoList": [{"Date": "2022-01-01 10:00:00", "Link": "https://www.tiktokv.com/share/video/9/"}]},
        "Like List": {"ItemFavoriteList": [{"Date": "2022-01-01 10:00:00", "Link": "https://www.tiktokv.com/share/video/9/"}]},
    }
}


def test_new_json_format(tmp_path):
    p = tmp_path / "user_data_tiktok.json"
    p.write_text(json.dumps(NEW_FORMAT))
    data = load_export(p)
    s = data.summary()
    assert s["watched"] == 4 and s["liked"] == 1 and s["favorited"] == 1
    assert s["searches"] == 1 and s["following"] == 1 and s["favorite_hashtags"] == 1
    assert s["shared"] == 1 and s["comments"] == 1
    est = data.estimated_watch_seconds()
    assert est["111"] == pytest.approx(2)
    assert est["222"] == pytest.approx(45)
    assert "333" not in est  # 5h gap = new session, unknown
    assert data.positive_ids() == {"222", "333", "555"}
    seeds = data.seed_interests()
    assert seeds["#kittens"] > 0 and seeds["@catlady"] > 0 and seeds["#cat"] > 0


def test_old_json_format_in_zip(tmp_path):
    z = tmp_path / "export.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("TikTok_Data/user_data.json", json.dumps(OLD_FORMAT))
    data = load_export(z)
    assert len(data.watched) == 1 and len(data.liked) == 1


def test_txt_format_folder(tmp_path):
    (tmp_path / "Watch History.txt").write_text(
        "Date: 2026-01-01 10:00:00\nLink: https://www.tiktokv.com/share/video/1/\n\n"
        "Date: 2026-01-01 10:00:20\nLink: https://www.tiktokv.com/share/video/2/\n"
    )
    (tmp_path / "Like List.txt").write_text("Date: 2026-01-01 10:00:10\nLink: https://www.tiktokv.com/share/video/1/\n")
    (tmp_path / "Searches.txt").write_text("Date: 2026-01-01 09:00:00\nSearch Term: pasta\n")
    data = load_export(tmp_path)
    assert len(data.watched) == 2 and len(data.liked) == 1 and len(data.searches) == 1
    assert data.estimated_watch_seconds()["1"] == pytest.approx(20)
