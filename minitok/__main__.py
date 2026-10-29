"""Command line entry point.

    minitok                      open the app
    minitok --source @creator    add a source (repeatable) and open
    minitok --folder ./videos    use a folder of local videos and open
    minitok demo [FOLDER]        create offline demo videos and open them
    minitok learn EXPORT.zip     print what your TikTok export says about your taste
"""

from __future__ import annotations

import argparse
import sys

from .config import AppConfig, data_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="minitok", description="A mini TikTok with an algorithm you control.")
    sub = parser.add_subparsers(dest="cmd")
    demo = sub.add_parser("demo", help="create offline demo videos (needs ffmpeg) and open them")
    demo.add_argument("folder", nargs="?", default=None)
    demo.add_argument("--count", type=int, default=12)
    learn = sub.add_parser("learn", help="analyse a TikTok data export without opening the app")
    learn.add_argument("export")
    learn.add_argument("--lookup", type=int, default=0, help="look up N videos online to learn weights")
    parser.add_argument("--source", action="append", default=[], help="@creator, #hashtag or URL (repeatable)")
    parser.add_argument("--folder", help="folder of local video files")
    parser.add_argument("--reset", action="store_true", help="forget all settings and learned interests")
    args = parser.parse_args(argv)

    config = AppConfig() if args.reset else AppConfig.load()

    if args.cmd == "learn":
        return _learn(args.export, args.lookup, config)
    if args.cmd == "demo":
        from .demo import make_demo

        folder = args.folder or str(data_dir() / "demo_videos")
        print(f"Creating demo videos in {folder} …")
        make_demo(folder, args.count)
        config.local_folder = str(folder)
    if args.folder:
        config.local_folder = args.folder
    for spec in args.source:
        if spec not in config.sources:
            config.sources.append(spec)
    config.save()

    from .ui import run

    return run(sys.argv[:1], config)


def _learn(path: str, lookup: int, config: AppConfig) -> int:
    from .algorithm import learner
    from .algorithm.tiktok_export import load_export

    data = load_export(path)
    if data.is_empty():
        print("That doesn't look like a TikTok data export.")
        return 1
    print("Found:", ", ".join(f"{v} {k}" for k, v in data.summary().items() if v))
    examples = []
    if lookup:
        from .downloader import Downloader
        from .storage import Cache

        dl = Downloader(Cache(data_dir() / "cache"), config)
        examples = learner.examples_from_export(
            data, dl.metadata, sample=lookup,
            progress=lambda i, n, _v: print(f"\rLooking up {i}/{n}", end="", flush=True))
        print()
    result = learner.learn(examples, data.seed_interests(), data.watch_stats())
    print("\n".join(result.report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
