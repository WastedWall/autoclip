"""Camera planning.

Following a talking head sample by sample makes the frame twitch with every nod,
and no amount of filtering turns that into calm footage. A locked-off camera is
what viewers read as steady, so the camera here holds still and only moves when
the subject has really gone somewhere else.

The whole track is known before the first frame is rendered, so nothing has to
react late. The track is split into the longest runs that fit inside the safe
zone, each run gets one fixed position, and an eased move joins neighbouring
runs:

1. **Rolling median** ignores head sway and detection glitches.
2. **Runs** fit inside the safe zone. A run shorter than the minimum hold was a
   passing lean, so it joins its neighbour.
3. **Moves** ease in and out, centred on the point where the subject left.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from .croppath import MAX_KEYFRAMES_PER_SEGMENT

Sample = tuple[float, float]

#: Straight pieces approximating the ease of one move.
MOVE_STEPS = 5

#: Keyframes kept free for the hold before the first move and the segment ends.
RESERVED_KEYFRAMES = 3


@dataclass
class CameraConfig:
    #: Drift the frame tolerates either side of its position, as a fraction of
    #: the crop size.
    safe_zone_ratio: float = 0.15
    #: Length of the rolling median.
    settle_s: float = 1.5
    #: Shortest time the frame holds still. Must be longer than a move.
    hold_s: float = 3.0
    #: Length of one move.
    move_s: float = 1.0


def plan_camera(
    samples: list[Sample], safe_zone_px: float, config: CameraConfig | None = None
) -> list[Sample]:
    """Turn ``(timestamp, position)`` samples into camera keyframes.

    Preconditions:
        samples are sorted by timestamp and non-empty.
    """
    config = config or CameraConfig()

    settled = _rolling_median(samples, config.settle_s)
    runs = _merge_short_runs(_split_runs(settled, safe_zone_px), config.hold_s)

    # Runs either side of a passing lean often agree, so no move is needed there.
    stops = [(runs[0][0][0], _position(runs[0]))]
    for run in runs[1:]:
        position = _position(run)
        if abs(position - stops[-1][1]) > safe_zone_px:
            stops.append((run[0][0], position))

    plan = [(samples[0][0], stops[0][1])]
    moves = len(stops) - 1
    if not moves:
        return plan

    # Fewer steps per move beats uneven thinning of the keyframes later on.
    budget = (MAX_KEYFRAMES_PER_SEGMENT - RESERVED_KEYFRAMES) // moves - 1
    steps = max(1, min(MOVE_STEPS, budget))

    for (_, before), (arrival, after) in zip(stops, stops[1:], strict=False):
        start = arrival - config.move_s / 2
        for step in range(steps + 1):
            progress = step / steps
            eased = progress * progress * (3 - 2 * progress)
            plan.append((start + progress * config.move_s, before + (after - before) * eased))

    return plan


def value_at(plan: list[Sample], timestamp: float) -> float:
    """Read a plan at any time, holding its first and last values at the ends."""
    if timestamp <= plan[0][0]:
        return plan[0][1]

    for (t0, v0), (t1, v1) in zip(plan, plan[1:], strict=False):
        if timestamp <= t1:
            return v0 + (v1 - v0) * (timestamp - t0) / (t1 - t0)

    return plan[-1][1]


def _rolling_median(samples: list[Sample], window_s: float) -> list[Sample]:
    half = window_s / 2
    return [
        (t, statistics.median(value for other, value in samples if abs(other - t) <= half))
        for t, _ in samples
    ]


def _split_runs(samples: list[Sample], safe_zone_px: float) -> list[list[Sample]]:
    """Split samples into the longest runs a single frame position can hold."""
    runs: list[list[Sample]] = []
    current: list[Sample] = []
    low = high = samples[0][1]

    for sample in samples:
        low = min(low, sample[1])
        high = max(high, sample[1])
        if high - low > 2 * safe_zone_px:
            runs.append(current)
            current = []
            low = high = sample[1]
        current.append(sample)

    runs.append(current)
    return runs


def _merge_short_runs(runs: list[list[Sample]], hold_s: float) -> list[list[Sample]]:
    """Join every run too short to hold into the run before it."""
    merged: list[list[Sample]] = []
    for run in runs:
        if merged and _duration(run) < hold_s:
            merged[-1] = merged[-1] + run
        else:
            merged.append(run)

    # The first run has nothing before it, so it joins the run after it.
    if len(merged) > 1 and _duration(merged[0]) < hold_s:
        merged[:2] = [merged[0] + merged[1]]

    return merged


def _duration(run: list[Sample]) -> float:
    return run[-1][0] - run[0][0]


def _position(run: list[Sample]) -> float:
    return statistics.median(value for _, value in run)
