"""Built-in algorithm types you can pick in the For You settings."""

from __future__ import annotations

from dataclasses import dataclass

from .features import FEATURE_NAMES


@dataclass(frozen=True)
class Preset:
    key: str
    name: str
    description: str
    weights: dict[str, float]
    exploration: float  # chance of showing a random candidate instead of the top pick
    diversity: float  # penalty for showing the same creator again soon (0..1)


def _w(**kw: float) -> dict[str, float]:
    return {name: float(kw.get(name, 0.0)) for name in FEATURE_NAMES}


PRESETS: dict[str, Preset] = {
    p.key: p
    for p in [
        Preset(
            "for_you",
            "For You (TikTok-style)",
            "Interest first, then engagement quality, a bit of freshness and exploration. "
            "Learns from what you watch fully, rewatch, like or skip.",
            _w(interest=3.0, like_rate=1.5, comment_rate=0.5, share_rate=1.0, popularity=0.6,
               freshness=0.8, duration_fit=0.4, novelty=0.6, random=0.3),
            exploration=0.10,
            diversity=0.5,
        ),
        Preset(
            "popular",
            "Most Popular",
            "Biggest videos first: views and likes dominate, your interests barely matter.",
            _w(popularity=3.0, like_rate=1.0, share_rate=0.5, interest=0.3, random=0.1),
            exploration=0.02,
            diversity=0.3,
        ),
        Preset(
            "fresh",
            "Fresh / Newest",
            "Newest uploads first, lightly filtered by quality.",
            _w(freshness=3.0, like_rate=0.5, interest=0.5, random=0.1),
            exploration=0.05,
            diversity=0.3,
        ),
        Preset(
            "niche",
            "Niche / Interests only",
            "Only what matches your interests; popularity is ignored.",
            _w(interest=4.0, duration_fit=0.3, novelty=0.3, random=0.1),
            exploration=0.03,
            diversity=0.4,
        ),
        Preset(
            "discovery",
            "Discovery / Small creators",
            "Pushes small and unseen creators; penalises viral videos.",
            _w(novelty=2.0, random=1.5, popularity=-1.0, like_rate=0.8, interest=0.5),
            exploration=0.30,
            diversity=0.8,
        ),
        Preset(
            "random",
            "Random",
            "No algorithm at all - pure shuffle.",
            _w(random=1.0),
            exploration=1.0,
            diversity=0.0,
        ),
        Preset(
            "custom",
            "Custom weights",
            "Set every weight yourself with the sliders.",
            _w(interest=1.0, like_rate=1.0, popularity=1.0, freshness=1.0, random=0.3),
            exploration=0.10,
            diversity=0.5,
        ),
        Preset(
            "formula",
            "Custom formula",
            "Type your own ranking formula (see the help next to the box).",
            _w(),
            exploration=0.05,
            diversity=0.3,
        ),
        Preset(
            "learned",
            "Learned from my data",
            "Weights and interests fitted from your TikTok data export and/or your history in this app.",
            _w(interest=2.0, like_rate=1.0, random=0.2),
            exploration=0.08,
            diversity=0.5,
        ),
    ]
}

DEFAULT_PRESET = "for_you"
