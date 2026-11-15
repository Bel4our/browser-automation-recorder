import json
import subprocess
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import apply_zoom


def test_ramp_frame_calculation_and_easing():
    events = [
        {"t": 2.0, "x": 640, "y": 360, "type": "in"},
        {"t": 5.0, "x": 640, "y": 360, "type": "out"},
    ]
    segments = apply_zoom.parse_zoom_segments(events, zoom_factor=2.0, ramp_sec=0.5, fps=25)
    assert len(segments) == 1
    seg = segments[0]
    assert seg["ramp_frames"] == 12
    assert seg["f_in_start"] == 50
    assert seg["f_in_end"] == 62
    assert seg["f_out_start"] == 125
    assert seg["f_out_end"] == 137

    z_before, _, _ = apply_zoom.evaluate_zoom_at_frame(49, segments)
    z_start, _, _ = apply_zoom.evaluate_zoom_at_frame(50, segments)
    z_mid, _, _ = apply_zoom.evaluate_zoom_at_frame(56, segments)
    z_hold, _, _ = apply_zoom.evaluate_zoom_at_frame(90, segments)
    z_out_mid, _, _ = apply_zoom.evaluate_zoom_at_frame(131, segments)
    z_after, _, _ = apply_zoom.evaluate_zoom_at_frame(137, segments)

    assert z_before == pytest.approx(1.0)
    assert z_start == pytest.approx(1.0)
    assert z_mid == pytest.approx(1.5)
    assert z_hold == pytest.approx(2.0)
    assert z_out_mid == pytest.approx(1.5)
    assert z_after == pytest.approx(1.0)


def test_clamping_coordinates():
    cx, cy = apply_zoom.clamp_crop_origin(0, 0, 1280, 720, 2.0)
    assert (cx, cy) == (0.0, 0.0)

    cx, cy = apply_zoom.clamp_crop_origin(1280, 720, 1280, 720, 2.0)
    assert (cx, cy) == (640.0, 360.0)

    cx, cy = apply_zoom.clamp_crop_origin(-100, 1000, 1280, 720, 2.0)
    assert (cx, cy) == (0.0, 360.0)

    cx, cy = apply_zoom.clamp_crop_origin(640, 360, 1280, 720, 2.0)
    assert (cx, cy) == (320.0, 180.0)


def test_multiple_non_overlapping_pairs():
    events = [
        {"t": 1.0, "x": 200, "y": 150, "type": "in"},
        {"t": 3.0, "x": 200, "y": 150, "type": "out"},
        {"t": 5.0, "x": 900, "y": 500, "type": "in"},
        {"t": 7.0, "x": 900, "y": 500, "type": "out"},
    ]
    segments = apply_zoom.parse_zoom_segments(events, zoom_factor=2.0, ramp_sec=0.4, fps=25)
    assert len(segments) == 2

    z1, x1, y1 = apply_zoom.evaluate_zoom_at_frame(50, segments)
    z_gap, x_gap, y_gap = apply_zoom.evaluate_zoom_at_frame(100, segments)
    z2, x2, y2 = apply_zoom.evaluate_zoom_at_frame(150, segments)

    assert (z1, x1, y1) == (2.0, 200.0, 150.0)
    assert (z_gap, x_gap, y_gap) == (1.0, None, None)
    assert (z2, x2, y2) == (2.0, 900.0, 500.0)


def test_open_ended_zoom_holds_to_end():
    events = [{"t": 2.0, "x": 400, "y": 300, "type": "in"}]
    segments = apply_zoom.parse_zoom_segments(events, zoom_factor=2.5, ramp_sec=0.5, fps=25)
    assert len(segments) == 1
    assert segments[0]["f_out_start"] is None
    assert segments[0]["f_out_end"] is None

    z_late, x_late, y_late = apply_zoom.evaluate_zoom_at_frame(10000, segments)
    assert z_late == pytest.approx(2.5)
    assert (x_late, y_late) == (400.0, 300.0)

    z_expr, x_expr, y_expr = apply_zoom.build_zoompan_expressions(segments, 1280, 720)
    assert "gte(on,62)" in z_expr
    assert "gte(on,50)" in x_expr
    assert "clip(" in y_expr


def test_old_sidecar_without_type():
    events = [
        {"t": 1.0, "x": 300, "y": 200},
        {"t": 4.0, "x": 300, "y": 200},
        {"t": 6.0, "x": 700, "y": 400},
    ]
    segments = apply_zoom.parse_zoom_segments(events, zoom_factor=2.0, ramp_sec=0.5, fps=25)
    assert len(segments) == 2
    assert segments[0]["f_out_start"] == 100
    assert segments[1]["f_out_start"] is None


def test_no_events_copy_unchanged(tmp_path, monkeypatch):
    video = tmp_path / "sample.webm"
    video.write_bytes(b"WEBM_BYTES_UNCHANGED")
    sidecar = tmp_path / "sample.webm.zoom.json"
    sidecar.write_text(json.dumps({"events": []}), encoding="utf-8")
    out = tmp_path / "sample_zoomed.webm"

    called = {"count": 0}

    def fake_run(*args, **kwargs):
        called["count"] += 1

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = apply_zoom.apply_zoom(video, output_path=out)
    assert result == out
    assert out.read_bytes() == b"WEBM_BYTES_UNCHANGED"
    assert called["count"] == 0


def test_missing_sidecar_copy_unchanged(tmp_path):
    video = tmp_path / "plain.webm"
    video.write_bytes(b"PLAIN_WEBM_DATA")
    out = tmp_path / "plain_zoomed.webm"

    result = apply_zoom.apply_zoom(video, output_path=out)
    assert result == out
    assert out.read_bytes() == b"PLAIN_WEBM_DATA"


def test_dry_run_prints_command_without_encoding(tmp_path, monkeypatch, capsys):
    video = tmp_path / "demo.webm"
    video.write_bytes(b"DUMMY")
    sidecar = tmp_path / "demo.webm.zoom.json"
    sidecar.write_text(
        json.dumps({
            "viewport": {"width": 1280, "height": 720},
            "fps": 25,
            "zoom_factor": 2.0,
            "ramp_sec": 0.5,
            "events": [
                {"t": 1.0, "x": 640, "y": 360, "type": "in"},
                {"t": 3.0, "x": 640, "y": 360, "type": "out"},
            ],
        }),
        encoding="utf-8",
    )

    called = {"count": 0}

    def fake_run(*args, **kwargs):
        called["count"] += 1

    monkeypatch.setattr(subprocess, "run", fake_run)
    apply_zoom.apply_zoom(video, ffmpeg_path="/opt/bin/ffmpeg", dry_run=True)
    captured = capsys.readouterr()

    assert called["count"] == 0
    assert "/opt/bin/ffmpeg" in captured.out
    assert "zoompan=" in captured.out
    assert "libvpx-vp9" in captured.out


def test_docker_integration_config():
    root = Path(__file__).resolve().parent.parent
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    service_js = (root / "web_service.js").read_text(encoding="utf-8")

    assert "ffmpeg" in dockerfile
    assert "python3" in dockerfile
    assert "apply_zoom.py" in service_js
    assert "/jobs/:id/process" in service_js
    assert "processed_video_url" in service_js