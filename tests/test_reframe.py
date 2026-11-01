"""Crop path construction, camera planning, and tracking.

These are the acceptance-bar mechanics from PRD 6.4 expressed as unit tests:
jitter suppression, no interpolation across cuts, and correct crop geometry.
"""

from __future__ import annotations

import math

import pytest
from autoclip.pipeline.reframe import ReframeConfig, _track_segment, croppath
from autoclip.pipeline.reframe.camera import plan_camera, value_at
from autoclip.pipeline.reframe.croppath import (
    CropKeyframe,
    CropSegment,
    Strategy,
    axis_expression,
    centre_crop,
    decimate,
    segment_crop_filter,
    target_crop_size,
)
from autoclip.pipeline.reframe.faces import FaceObservation
from autoclip.pipeline.reframe.tracker import build_tracks


class TestTargetCropSize:
    def test_landscape_to_vertical(self) -> None:
        # 1080 * 9/16 = 607.5, rounded to the nearest even width.
        assert target_crop_size(1920, 1080, 9, 16) == (608, 1080)

    def test_square_output(self) -> None:
        assert target_crop_size(1920, 1080, 1, 1) == (1080, 1080)

    def test_landscape_source_to_landscape_output_is_full_frame(self) -> None:
        assert target_crop_size(1920, 1080, 16, 9) == (1920, 1080)

    def test_vertical_source_to_vertical_output_is_full_frame(self) -> None:
        assert target_crop_size(1080, 1920, 9, 16) == (1080, 1920)

    @pytest.mark.parametrize(
        ("sw", "sh"), [(1920, 1080), (1280, 720), (3840, 2160), (1440, 1080), (1920, 800)]
    )
    def test_dimensions_are_always_even(self, sw: int, sh: int) -> None:
        # Odd dimensions fail an h264 yuv420p encode outright.
        width, height = target_crop_size(sw, sh, 9, 16)

        assert width % 2 == 0
        assert height % 2 == 0

    def test_crop_never_exceeds_the_source(self) -> None:
        width, height = target_crop_size(1920, 1080, 9, 16)

        assert width <= 1920
        assert height <= 1080


class TestCentreCrop:
    def test_produces_one_static_segment(self) -> None:
        path = centre_crop(1920, 1080, 30.0)

        assert len(path.segments) == 1
        assert path.segments[0].is_static
        assert path.segments[0].strategy is Strategy.GENERAL

    def test_is_horizontally_centred(self) -> None:
        path = centre_crop(1920, 1080, 30.0)
        segment = path.segments[0]

        assert segment.keyframes[0].x == pytest.approx((1920 - segment.width) / 2)

    def test_spans_the_full_duration(self) -> None:
        path = centre_crop(1920, 1080, 42.5)

        assert path.segments[0].end_s == 42.5
        assert path.duration_s == 42.5


def relocation(at_s: float, distance: float = 300.0, duration_s: float = 30.0) -> list:
    """A subject swaying gently who moves to a new spot once."""
    samples = []
    for i in range(int(duration_s * 5)):
        t = i * 0.2
        samples.append((t, 500 + math.sin(t) * 40 + (distance if t >= at_s else 0)))
    return samples


class TestPlanCamera:
    def test_head_sway_never_moves_the_frame(self) -> None:
        samples = [(i * 0.2, 500 + math.sin(i * 0.2) * 40) for i in range(150)]

        assert len(plan_camera(samples, 90.0)) == 1

    def test_detection_glitch_never_moves_the_frame(self) -> None:
        samples = [(i * 0.2, 5000.0 if i == 20 else 500.0) for i in range(60)]

        assert plan_camera(samples, 90.0) == [(0.0, 500.0)]

    def test_passing_lean_never_moves_the_frame(self) -> None:
        samples = [(i * 0.2, 800.0 if 40 <= i < 50 else 500.0) for i in range(100)]

        assert len(plan_camera(samples, 90.0)) == 1

    def test_real_relocation_makes_one_move(self) -> None:
        plan = plan_camera(relocation(at_s=15.0), 90.0)

        assert plan[-1][1] - plan[0][1] == pytest.approx(300, abs=40)
        assert plan[-1][0] - plan[1][0] == pytest.approx(1.0)
        assert plan[1][0] == pytest.approx(14.5, abs=1.0)

    def test_move_eases_in_and_out(self) -> None:
        plan = plan_camera(relocation(at_s=15.0), 90.0)[1:]
        speeds = [(v1 - v0) / (t1 - t0) for (t0, v0), (t1, v1) in zip(plan, plan[1:], strict=False)]

        assert speeds == sorted(speeds[:3]) + sorted(speeds[2:], reverse=True)[1:]
        assert max(speeds) > 2 * speeds[0]

    def test_frame_holds_between_moves(self) -> None:
        samples = [(i * 0.2, 300.0 * (i // 25)) for i in range(125)]

        plan = plan_camera(samples, 90.0)

        starts = [t0 for (t0, v0), (_, v1) in zip(plan, plan[1:], strict=False) if v0 == v1]
        assert len(starts) == 4
        for (t0, _), (t1, _) in zip(plan, plan[1:], strict=False):
            assert t1 > t0

    def test_short_clip_at_the_start_joins_the_next_position(self) -> None:
        samples = [(i * 0.2, 800.0 if i < 5 else 500.0) for i in range(60)]

        assert len(plan_camera(samples, 90.0)) == 1

    def test_restless_subject_stays_inside_the_keyframe_budget(self) -> None:
        samples = [(i * 0.2, 300.0 * ((i // 20) % 2)) for i in range(450)]

        assert len(plan_camera(samples, 90.0)) <= croppath.MAX_KEYFRAMES_PER_SEGMENT

    def test_single_sample(self) -> None:
        assert plan_camera([(0.0, 100.0)], 90.0) == [(0.0, 100.0)]

    def test_value_at_interpolates_and_holds_the_ends(self) -> None:
        plan = [(1.0, 10.0), (3.0, 30.0)]

        assert value_at(plan, 0.0) == 10.0
        assert value_at(plan, 2.0) == 20.0
        assert value_at(plan, 9.0) == 30.0


class TestTrackSegment:
    def build(self, cx: list[float]):
        observations = [
            FaceObservation(t=i * 0.2, cx=x, cy=500, width=150, height=200, eye_y=450, mar=0.05)
            for i, x in enumerate(cx)
        ]
        tracks = build_tracks(observations)

        return _track_segment(
            tracks[0],
            start_s=0.0,
            end_s=len(cx) * 0.2,
            source_w=1920,
            source_h=1080,
            crop_w=608,
            crop_h=1080,
            config=ReframeConfig(),
        )

    def test_swaying_subject_gets_a_static_frame(self) -> None:
        segment = self.build([960 + math.sin(i * 0.2) * 40 for i in range(150)])

        assert segment.is_static
        assert segment.keyframes[0].x == pytest.approx(656, abs=30)

    def test_relocating_subject_gets_keyframes_for_the_whole_segment(self) -> None:
        walk = [600.0 + step * 25 for step in range(20)]
        segment = self.build([600.0] * 65 + walk + [1100.0] * 65)

        assert not segment.is_static
        assert segment.keyframes[0].t == 0.0
        assert segment.keyframes[-1].t == pytest.approx(30.0)
        assert segment.keyframes[0].x == pytest.approx(296)
        assert segment.keyframes[-1].x == pytest.approx(796)


class TestAxisExpression:
    def test_single_keyframe_is_a_constant(self) -> None:
        assert axis_expression([CropKeyframe(0.0, 100.0, 50.0)], "x") == "100.0"

    def test_two_keyframes_produce_a_ramp(self) -> None:
        frames = [CropKeyframe(0.0, 0.0, 0.0), CropKeyframe(2.0, 200.0, 0.0)]

        expression = axis_expression(frames, "x")

        assert "if(lt(t,2.0)" in expression
        assert "100.0" in expression  # slope: 200px over 2s

    def test_offset_rebases_times(self) -> None:
        frames = [CropKeyframe(10.0, 0.0, 0.0), CropKeyframe(12.0, 200.0, 0.0)]

        expression = axis_expression(frames, "x", offset_s=10.0)

        assert "t-0.0" in expression
        assert "lt(t,2.0)" in expression

    def test_expression_evaluates_correctly_at_keyframes(self) -> None:
        frames = [
            CropKeyframe(0.0, 0.0, 0.0),
            CropKeyframe(1.0, 100.0, 0.0),
            CropKeyframe(2.0, 50.0, 0.0),
        ]

        expression = axis_expression(frames, "x")

        assert _evaluate(expression, 0.0) == pytest.approx(0.0, abs=0.1)
        assert _evaluate(expression, 0.5) == pytest.approx(50.0, abs=0.1)
        assert _evaluate(expression, 1.5) == pytest.approx(75.0, abs=0.1)

    def test_empty_keyframes_give_zero(self) -> None:
        assert axis_expression([], "x") == "0"


class TestDecimate:
    def test_collinear_keyframes_are_removed(self) -> None:
        frames = [CropKeyframe(float(i), float(i * 10), 0.0) for i in range(20)]

        assert len(decimate(frames)) == 2

    def test_direction_changes_are_kept(self) -> None:
        frames = [
            CropKeyframe(0.0, 0.0, 0.0),
            CropKeyframe(1.0, 100.0, 0.0),
            CropKeyframe(2.0, 0.0, 0.0),
        ]

        assert len(decimate(frames)) == 3

    def test_respects_the_limit(self) -> None:
        frames = [CropKeyframe(float(i), float(i % 2) * 100, 0.0) for i in range(200)]

        result = decimate(frames, limit=10)

        assert len(result) <= 10

    def test_endpoints_survive(self) -> None:
        frames = [CropKeyframe(float(i), float(i % 3) * 60, 0.0) for i in range(100)]

        result = decimate(frames, limit=8)

        assert result[0].t == 0.0
        assert result[-1].t == 99.0

    def test_times_stay_strictly_increasing(self) -> None:
        frames = [CropKeyframe(float(i), float(i % 5) * 40, 0.0) for i in range(150)]

        result = decimate(frames, limit=12)

        assert all(b.t > a.t for a, b in zip(result, result[1:], strict=False))


class TestSegmentFilter:
    def test_static_segment_uses_constants(self) -> None:
        segment = CropSegment(
            start_s=0.0,
            end_s=5.0,
            width=606,
            height=1080,
            keyframes=[CropKeyframe(0.0, 657.0, 0.0)],
        )

        assert segment_crop_filter(segment) == "crop=w=606:h=1080:x='657.0':y='0.0':exact=1"

    def test_moving_segment_uses_expressions(self) -> None:
        segment = CropSegment(
            start_s=0.0,
            end_s=4.0,
            width=606,
            height=1080,
            keyframes=[
                CropKeyframe(0.0, 100.0, 0.0),
                CropKeyframe(2.0, 300.0, 0.0),
                CropKeyframe(4.0, 200.0, 0.0),
            ],
        )

        result = segment_crop_filter(segment)

        # Commas are backslash-escaped for the filtergraph parser, which strips
        # them before the expression parser sees the string.
        assert "if(lt(t\\," in result

    def test_commas_are_escaped_for_the_filtergraph(self) -> None:
        segment = CropSegment(
            start_s=0.0,
            end_s=4.0,
            width=606,
            height=1080,
            keyframes=[
                CropKeyframe(0.0, 100.0, 0.0),
                CropKeyframe(2.0, 300.0, 0.0),
                CropKeyframe(4.0, 200.0, 0.0),
            ],
        )

        result = segment_crop_filter(segment)

        # An unescaped comma would terminate the filter and break the graph.
        assert ",'" not in result.replace("\\,", "")

    def test_near_static_path_collapses_to_a_constant(self) -> None:
        # Sub-pixel drift is not movement.
        segment = CropSegment(
            start_s=0.0,
            end_s=4.0,
            width=606,
            height=1080,
            keyframes=[CropKeyframe(float(i), 100.0 + i * 0.1, 0.0) for i in range(5)],
        )

        assert segment.is_static
        assert "if(" not in segment_crop_filter(segment)


class TestSerialisation:
    def test_crop_path_round_trips(self, tmp_path) -> None:
        original = croppath.CropPath(
            source_width=1920,
            source_height=1080,
            segments=[
                CropSegment(
                    start_s=0.0,
                    end_s=5.0,
                    width=606,
                    height=1080,
                    keyframes=[CropKeyframe(0.0, 100.0, 0.0), CropKeyframe(5.0, 200.0, 0.0)],
                    strategy=Strategy.TRACK,
                ),
                CropSegment(
                    start_s=5.0,
                    end_s=9.0,
                    width=1920,
                    height=1080,
                    keyframes=[CropKeyframe(5.0, 0.0, 0.0)],
                    strategy=Strategy.WIDE,
                    fit=True,
                ),
            ],
        )

        restored = croppath.CropPath.from_dict(original.to_dict())

        assert restored.source_width == 1920
        assert len(restored.segments) == 2
        assert restored.segments[0].strategy is Strategy.TRACK
        assert restored.segments[1].fit is True

    def test_saves_and_loads_from_disk(self, tmp_path) -> None:
        path = centre_crop(1920, 1080, 12.0)
        target = path.save(tmp_path / "crop.json")

        assert croppath.CropPath.load(target).duration_s == 12.0


class TestTracking:
    def _observation(self, t: float, cx: float, cy: float = 400.0) -> FaceObservation:
        return FaceObservation(t=t, cx=cx, cy=cy, width=200, height=260, eye_y=cy - 40, mar=0.05)

    def test_a_moving_face_becomes_one_track(self) -> None:
        observations = [self._observation(i * 0.2, 500 + i * 5) for i in range(20)]

        tracks = build_tracks(observations)

        assert len(tracks) == 1
        assert len(tracks[0].observations) == 20

    def test_two_separated_faces_become_two_tracks(self) -> None:
        observations = [
            obs
            for i in range(20)
            for obs in (self._observation(i * 0.2, 400), self._observation(i * 0.2, 1400))
        ]

        tracks = build_tracks(observations)

        assert len(tracks) == 2

    def test_a_long_gap_splits_a_track(self) -> None:
        early = [self._observation(i * 0.2, 500) for i in range(10)]
        late = [self._observation(20 + i * 0.2, 500) for i in range(10)]

        tracks = build_tracks(early + late)

        assert len(tracks) == 2

    def test_transient_detections_are_dropped(self) -> None:
        stable = [self._observation(i * 0.2, 500) for i in range(20)]
        blip = [self._observation(1.0, 1700)]

        tracks = build_tracks(stable + blip)

        assert len(tracks) == 1

    def test_tracks_rank_by_prominence(self) -> None:
        big = [
            FaceObservation(t=i * 0.2, cx=500, cy=400, width=400, height=500, eye_y=360, mar=0.1)
            for i in range(20)
        ]
        small = [
            FaceObservation(t=i * 0.2, cx=1500, cy=400, width=90, height=110, eye_y=380, mar=0.1)
            for i in range(20)
        ]

        tracks = build_tracks(big + small)

        assert tracks[0].mean_area > tracks[1].mean_area

    def test_no_observations_gives_no_tracks(self) -> None:
        assert build_tracks([]) == []

    def test_mouth_activity_distinguishes_talking_from_still(self) -> None:
        talking = build_tracks(
            [
                FaceObservation(
                    t=i * 0.2,
                    cx=500,
                    cy=400,
                    width=200,
                    height=260,
                    eye_y=360,
                    mar=0.05 + (0.15 if i % 2 else 0.0),
                )
                for i in range(20)
            ]
        )[0]
        still = build_tracks(
            [
                FaceObservation(
                    t=i * 0.2, cx=1500, cy=400, width=200, height=260, eye_y=360, mar=0.05
                )
                for i in range(20)
            ]
        )[0]

        assert talking.mouth_activity(0.0, 4.0) > still.mouth_activity(0.0, 4.0)


def _evaluate(expression: str, t: float) -> float:
    """Evaluate an ffmpeg-style expression in Python, for test assertions only."""
    import re

    python_expression = re.sub(r"\blt\(", "_lt(", expression)
    python_expression = re.sub(r"\bif\(", "_if(", python_expression)
    return eval(  # noqa: S307 - test-only evaluation of expressions we generated
        python_expression,
        {
            "_if": lambda cond, a, b: a if cond else b,
            "_lt": lambda a, b: a < b,
            "t": t,
        },
    )
