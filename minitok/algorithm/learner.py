"""Fits your personal algorithm from data: which features predict that you enjoy a video.

Inputs can be (any mix of):
  * your in-app history (every video shown, with watch time / loops / likes);
  * your TikTok data export - watch history, likes, favourites, shares, searches,
    follows. The export only has links, so to learn *weights* we look up a
    sample of those videos' metadata (views, likes, hashtags, creator...).

Output: weights for each feature (a logistic regression, rescaled to the same
range the presets use), an interest profile (hashtags/creators/sounds with
log-odds weights), a preferred video length, and a readable report.

Honest caveat: TikTok's real recommender is a large neural model trained on
everyone's behaviour and is not public. What this recovers is *your* ranking
- the simplest model that best explains what you actually watched and liked.
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, field
from typing import Callable, Iterable

from ..events import Impression
from ..models import Video
from .features import FEATURE_NAMES, FEATURES, ViewerState, extract
from .tiktok_export import ExportData, unique_ids, video_url

# "random" carries no information and "interest" is learned separately (token profile)
LEARNED_FEATURES = [f for f in FEATURE_NAMES if f != "random"]
WEIGHT_SCALE = 3.0  # the strongest learned feature gets this weight


@dataclass
class Example:
    video: Video
    label: float  # 0..1
    weight: float = 1.0
    seen_authors: list[str] = field(default_factory=list)


@dataclass
class LearnResult:
    weights: dict[str, float]
    interests: dict[str, float]
    preferred_duration: float | None
    exploration: float
    examples: int
    positives: int
    accuracy: float
    report: list[str]


# -- tiny logistic regression ------------------------------------------------

def _sigmoid(z: float) -> float:
    if z < -40:
        return 0.0
    if z > 40:
        return 1.0
    return 1.0 / (1.0 + math.exp(-z))


def fit_logistic(
    rows: list[list[float]],
    labels: list[float],
    sample_weights: list[float] | None = None,
    l2: float = 0.05,
    epochs: int = 800,
    lr: float = 0.5,
) -> tuple[list[float], float]:
    """Batch gradient descent - the data sets here are small (hundreds of rows)."""
    if not rows:
        return [], 0.0
    n, d = len(rows), len(rows[0])
    sw = sample_weights or [1.0] * n
    total_w = sum(sw) or 1.0
    w = [0.0] * d
    b = 0.0
    for _ in range(epochs):
        gw = [0.0] * d
        gb = 0.0
        for x, y, s in zip(rows, labels, sw):
            err = (_sigmoid(sum(wi * xi for wi, xi in zip(w, x)) + b) - y) * s
            for j in range(d):
                gw[j] += err * x[j]
            gb += err
        for j in range(d):
            w[j] -= lr * (gw[j] / total_w + l2 * w[j])
        b -= lr * gb / total_w
    return w, b


# -- interest profile ---------------------------------------------------------

def token_profile(examples: Iterable[Example], min_count: int = 2, limit: int = 200) -> dict[str, float]:
    """Smoothed log-odds of each token appearing in liked vs. skipped videos."""
    pos: dict[str, float] = {}
    neg: dict[str, float] = {}
    n_pos = n_neg = 0.0
    for ex in examples:
        n_pos += ex.label * ex.weight
        n_neg += (1 - ex.label) * ex.weight
        for tok in ex.video.tokens():
            pos[tok] = pos.get(tok, 0.0) + ex.label * ex.weight
            neg[tok] = neg.get(tok, 0.0) + (1 - ex.label) * ex.weight
    profile = {}
    for tok in set(pos) | set(neg):
        p, q = pos.get(tok, 0.0), neg.get(tok, 0.0)
        if p + q < min_count:
            continue
        lo = math.log((p + 0.5) / (n_pos + 1.0)) - math.log((q + 0.5) / (n_neg + 1.0))
        if tok[0] not in "#@♪":
            lo *= 0.5  # caption words are noisier than tags/creators/sounds
        profile[tok] = max(-3.0, min(3.0, lo))
    ranked = sorted(profile.items(), key=lambda kv: abs(kv[1]) * math.sqrt(pos.get(kv[0], 0) + neg.get(kv[0], 0)),
                    reverse=True)
    return dict(ranked[:limit])


# -- building examples ----------------------------------------------------------

def examples_from_history(impressions: Iterable[Impression]) -> list[Example]:
    out: list[Example] = []
    recent: list[str] = []
    for imp in impressions:
        e = imp.engagement()
        # ambiguous middles carry less weight than clear skips / clear wins
        weight = 0.4 + 1.2 * abs(e - 0.5)
        out.append(Example(imp.video, 1.0 if e >= 0.6 else 0.0, weight, list(recent)))
        if imp.video.author:
            recent.append(imp.video.author.lower().lstrip("@"))
            recent = recent[-30:]
    return out


def examples_from_export(
    export: ExportData,
    fetch: Callable[[str], Video | None],
    sample: int = 60,
    progress: Callable[[int, int, str], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    rng: random.Random | None = None,
) -> list[Example]:
    """Look up metadata for a balanced sample of videos from the export and label them."""
    rng = rng or random.Random(7)
    watch = export.estimated_watch_seconds()
    positives = export.positive_ids()
    pos_ids = [v for v in unique_ids([*export.liked, *export.favorited, *export.shared])]
    pos_ids += [v for v, s in watch.items() if s >= 30 and v not in positives]
    neg_ids = [v for v, s in watch.items() if s <= 3 and v not in positives]
    rng.shuffle(pos_ids)
    rng.shuffle(neg_ids)
    half = max(1, sample // 2)
    chosen = pos_ids[:half] + neg_ids[: sample - min(half, len(pos_ids))]
    out: list[Example] = []
    for i, vid in enumerate(chosen):
        if should_stop and should_stop():
            break
        if progress:
            progress(i, len(chosen), vid)
        try:
            video = fetch(video_url(vid))
        except Exception:  # noqa: BLE001 - deleted/private videos are common in old history
            video = None
        if video is None:
            continue
        secs = watch.get(vid)
        if vid in positives:
            label = 1.0
        elif secs is not None and video.duration > 0 and secs >= 0.8 * video.duration:
            label = 1.0
        elif secs is not None and secs <= 3:
            label = 0.0
        elif secs is not None and secs >= 30:
            label = 1.0
        else:
            continue
        out.append(Example(video, label))
    if progress:
        progress(len(chosen), len(chosen), "")
    return out


# -- fitting ----------------------------------------------------------------------

def _feature_rows(examples: list[Example], profiles: list[dict[str, float]], folds: list[int],
                  preferred_duration: float) -> list[list[float]]:
    rng = random.Random(0)
    rows = []
    for ex, fold in zip(examples, folds):
        viewer = ViewerState(interests=profiles[fold], preferred_duration=preferred_duration,
                             recent_authors=ex.seen_authors)
        feats = extract(ex.video, viewer, rng)
        rows.append([feats[f] for f in LEARNED_FEATURES])
    return rows


def learn(
    examples: list[Example],
    seed_interests: dict[str, float] | None = None,
    export_stats: dict[str, float] | None = None,
) -> LearnResult:
    report: list[str] = []
    seed_interests = seed_interests or {}
    positives = sum(1 for e in examples if e.label >= 0.5)

    liked_durations = [e.video.duration for e in examples if e.label >= 0.5 and e.video.duration > 0]
    preferred = statistics.median(liked_durations) if liked_durations else None
    if export_stats and not preferred and export_stats.get("median_watch_seconds"):
        preferred = float(export_stats["median_watch_seconds"])

    full_profile = token_profile(examples)
    interests = dict(seed_interests)
    for tok, val in full_profile.items():
        interests[tok] = max(-3.0, min(3.0, interests.get(tok, 0.0) + val))

    if len(examples) < 8 or positives in (0, len(examples)):
        report.append(
            f"Only {len(examples)} usable examples ({positives} liked/finished) - not enough to fit "
            "weights yet. Interests were still learned; watch more videos in the app (or enrich more "
            "export videos) and fit again."
        )
        weights = {f: 0.0 for f in FEATURE_NAMES}
        weights.update(interest=2.0, like_rate=1.0, random=0.2)
        return LearnResult(weights, interests, preferred, 0.1, len(examples), positives, 0.0, report)

    # Cross-fitting: each example's "interest" feature is computed with a profile
    # learned from the *other* half, otherwise interest would look perfect by construction.
    idx = list(range(len(examples)))
    random.Random(1).shuffle(idx)
    folds = [0] * len(examples)
    for rank, i in enumerate(idx):
        folds[i] = rank % 2
    profiles = []
    for k in (0, 1):
        held_in = [ex for ex, f in zip(examples, folds) if f != k]
        prof = dict(seed_interests)
        for tok, val in token_profile(held_in).items():
            prof[tok] = max(-3.0, min(3.0, prof.get(tok, 0.0) + val))
        profiles.append(prof)

    rows = _feature_rows(examples, profiles, folds, preferred or 25.0)
    labels = [e.label for e in examples]
    sw = [e.weight for e in examples]
    # standardise so coefficients are comparable across features
    means = [statistics.fmean(col) for col in zip(*rows)]
    stds = [statistics.pstdev(col) or 1.0 for col in zip(*rows)]
    z = [[(x - m) / s for x, m, s in zip(r, means, stds)] for r in rows]
    coef, bias = fit_logistic(z, labels, sw)

    correct = sum(
        1 for r, y in zip(z, labels)
        if (_sigmoid(sum(c * x for c, x in zip(coef, r)) + bias) >= 0.5) == (y >= 0.5)
    )
    accuracy = correct / len(examples)

    # back to the raw 0..1 feature scale, then rescale so the biggest weight == WEIGHT_SCALE
    raw = [c / s for c, s in zip(coef, stds)]
    biggest = max((abs(v) for v in raw), default=0.0) or 1.0
    weights = {f: 0.0 for f in FEATURE_NAMES}
    for f, v in zip(LEARNED_FEATURES, raw):
        weights[f] = round(v / biggest * WEIGHT_SCALE, 3)
    weights["random"] = 0.2

    # exploration: if you often enjoy things that do NOT match your interests, explore more
    off_profile = [e for e in examples if e.label >= 0.5 and ViewerState(interests).interest_score(e.video) <= 0]
    exploration = round(min(0.3, max(0.03, 0.4 * len(off_profile) / max(1, positives))), 3)

    report.append(f"Fitted on {len(examples)} videos ({positives} you liked/finished, "
                  f"{len(examples) - positives} you skipped).")
    report.append(f"The model explains {accuracy:.0%} of your choices on that data "
                  f"(always guessing the majority would give {max(positives, len(examples) - positives) / len(examples):.0%}).")
    order = sorted(LEARNED_FEATURES, key=lambda f: abs(weights[f]), reverse=True)
    report.append("What drives your feed, strongest first:")
    for f in order:
        if abs(weights[f]) < 0.05:
            continue
        direction = "more" if weights[f] > 0 else "LESS"
        report.append(f"  {weights[f]:+.2f}  {f}: you want {direction} of this - {FEATURES[f].lower()}")
    if preferred:
        report.append(f"Preferred length: about {preferred:.0f}s.")
    top = [t for t, v in sorted(interests.items(), key=lambda kv: kv[1], reverse=True) if v > 0][:10]
    bottom = [t for t, v in sorted(interests.items(), key=lambda kv: kv[1]) if v < 0][:5]
    if top:
        report.append("Top interests: " + ", ".join(top))
    if bottom:
        report.append("You tend to skip: " + ", ".join(bottom))
    report.append(f"Suggested exploration: {exploration:.0%} of videos picked at random.")
    return LearnResult(weights, interests, preferred, exploration, len(examples), positives, accuracy, report)
