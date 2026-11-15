import argparse
import json
import math
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def load_sidecar(sidecar_path: Path) -> Optional[Dict]:
    if not sidecar_path.exists():
        return None
    raw = sidecar_path.read_text(encoding="utf-8").strip()
    if not raw:
        return None
    return json.loads(raw)


def normalize_events(events: List[Dict]) -> List[Dict]:
    normalized = []
    toggle_next = "in"
    for ev in events:
        ev_type = ev.get("type")
        if ev_type not in ("in", "out"):
            ev_type = toggle_next
            toggle_next = "out" if toggle_next == "in" else "in"
        normalized.append({
            "t": float(ev["t"]),
            "x": float(ev["x"]),
            "y": float(ev["y"]),
            "type": ev_type,
        })
    normalized.sort(key=lambda item: item["t"])
    return normalized


def parse_zoom_segments(
    events: List[Dict],
    zoom_factor: float = 2.0,
    ramp_sec: float = 0.5,
    fps: int = 25,
) -> List[Dict]:
    normalized = normalize_events(events)
    ramp_frames = max(0, round(ramp_sec * fps))
    segments = []
    pending_in = None

    for ev in normalized:
        if ev["type"] == "in":
            if pending_in is None:
                pending_in = ev
        elif ev["type"] == "out":
            if pending_in is not None:
                f_in_start = max(0, round(pending_in["t"] * fps))
                f_in_end = f_in_start + ramp_frames
                f_out_start = max(f_in_end, round(ev["t"] * fps))
                f_out_end = f_out_start + ramp_frames
                segments.append({
                    "f_in_start": f_in_start,
                    "f_in_end": f_in_end,
                    "f_out_start": f_out_start,
                    "f_out_end": f_out_end,
                    "x": pending_in["x"],
                    "y": pending_in["y"],
                    "zoom_factor": float(zoom_factor),
                    "ramp_frames": ramp_frames,
                })
                pending_in = None

    if pending_in is not None:
        f_in_start = max(0, round(pending_in["t"] * fps))
        f_in_end = f_in_start + ramp_frames
        segments.append({
            "f_in_start": f_in_start,
            "f_in_end": f_in_end,
            "f_out_start": None,
            "f_out_end": None,
            "x": pending_in["x"],
            "y": pending_in["y"],
            "zoom_factor": float(zoom_factor),
            "ramp_frames": ramp_frames,
        })

    return segments


def clamp_crop_origin(px: float, py: float, width: int, height: int, zoom: float) -> Tuple[float, float]:
    crop_w = width / zoom
    crop_h = height / zoom
    max_x = max(0.0, width - crop_w)
    max_y = max(0.0, height - crop_h)
    cx = max(0.0, min(max_x, px - crop_w / 2.0))
    cy = max(0.0, min(max_y, py - crop_h / 2.0))
    return cx, cy


def evaluate_zoom_at_frame(frame: int, segments: List[Dict]) -> Tuple[float, Optional[float], Optional[float]]:
    for seg in segments:
        f_in_s = seg["f_in_start"]
        f_in_e = seg["f_in_end"]
        f_out_s = seg["f_out_start"]
        f_out_e = seg["f_out_end"]
        rf = seg["ramp_frames"]
        z_target = seg["zoom_factor"]

        if f_out_s is None:
            if frame < f_in_s:
                continue
            if rf > 0 and f_in_s <= frame < f_in_e:
                p = (frame - f_in_s) / rf
                ease = 0.5 * (1.0 - math.cos(math.pi * p))
                return 1.0 + (z_target - 1.0) * ease, seg["x"], seg["y"]
            return z_target, seg["x"], seg["y"]

        if f_in_s <= frame < f_out_e:
            if rf > 0 and f_in_s <= frame < f_in_e:
                p = (frame - f_in_s) / rf
                ease = 0.5 * (1.0 - math.cos(math.pi * p))
                return 1.0 + (z_target - 1.0) * ease, seg["x"], seg["y"]
            if f_in_e <= frame < f_out_s:
                return z_target, seg["x"], seg["y"]
            if rf > 0 and f_out_s <= frame < f_out_e:
                p = (frame - f_out_s) / rf
                ease = 0.5 * (1.0 - math.cos(math.pi * p))
                return z_target - (z_target - 1.0) * ease, seg["x"], seg["y"]

    return 1.0, None, None


def _fmt_num(val: float) -> str:
    s = f"{val:.4f}".rstrip("0").rstrip(".")
    return s if s else "0"


def build_zoompan_expressions(segments: List[Dict], width: int, height: int) -> Tuple[str, str, str]:
    z_acc = "1"
    px_acc = "iw/2"
    py_acc = "ih/2"

    for seg in reversed(segments):
        z_str = _fmt_num(seg["zoom_factor"])
        rf = seg["ramp_frames"]
        f_in_s = seg["f_in_start"]
        f_in_e = seg["f_in_end"]
        f_out_s = seg["f_out_start"]
        f_out_e = seg["f_out_end"]

        if f_out_s is None:
            z_branch = f"if(gte(on,{f_in_e}),{z_str},{z_acc})"
            if rf > 0:
                ramp_in = f"1+({z_str}-1)*0.5*(1-cos(PI*(on-{f_in_s})/{rf}))"
                z_branch = f"if(between(on,{f_in_s},{f_in_e - 1}),{ramp_in},{z_branch})"
            z_acc = z_branch
            pos_cond = f"gte(on,{f_in_s})"
        else:
            z_branch = z_acc
            if rf > 0:
                ramp_out = f"{z_str}-({z_str}-1)*0.5*(1-cos(PI*(on-{f_out_s})/{rf}))"
                z_branch = f"if(between(on,{f_out_s},{f_out_e - 1}),{ramp_out},{z_branch})"
            if f_out_s > f_in_e:
                z_branch = f"if(between(on,{f_in_e},{f_out_s - 1}),{z_str},{z_branch})"
            if rf > 0:
                ramp_in = f"1+({z_str}-1)*0.5*(1-cos(PI*(on-{f_in_s})/{rf}))"
                z_branch = f"if(between(on,{f_in_s},{f_in_e - 1}),{ramp_in},{z_branch})"
            z_acc = z_branch
            pos_cond = f"between(on,{f_in_s},{f_out_e - 1})"

        px_str = _fmt_num(seg["x"])
        py_str = _fmt_num(seg["y"])
        px_acc = f"if({pos_cond},{px_str},{px_acc})"
        py_acc = f"if({pos_cond},{py_str},{py_acc})"

    x_expr = f"clip(({px_acc})-iw/(2*zoom),0,iw-iw/zoom)"
    y_expr = f"clip(({py_acc})-ih/(2*zoom),0,ih-ih/zoom)"
    return z_acc, x_expr, y_expr


def build_ffmpeg_command(
    video_path: Path,
    output_path: Path,
    segments: List[Dict],
    width: int = 1280,
    height: int = 720,
    fps: int = 25,
    ffmpeg_path: str = "ffmpeg",
) -> List[str]:
    z_expr, x_expr, y_expr = build_zoompan_expressions(segments, width, height)
    vf = f"fps={fps},zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d=1:s={width}x{height}:fps={fps}"
    return [
        ffmpeg_path,
        "-y",
        "-i",
        str(video_path),
        "-vf",
        vf,
        "-c:v",
        "libvpx-vp9",
        "-crf",
        "30",
        "-b:v",
        "0",
        "-cpu-used",
        "4",
        "-row-mt",
        "1",
        "-an",
        str(output_path),
    ]


def resolve_sidecar_path(video_path: Path, sidecar_path: Optional[Path] = None) -> Path:
    if sidecar_path is not None:
        return sidecar_path
    primary = Path(f"{video_path}.zoom.json")
    if primary.exists():
        return primary
    legacy = video_path.with_suffix(".zoom.json")
    if legacy.exists():
        return legacy
    return primary


def resolve_output_path(video_path: Path, output_path: Optional[Path] = None) -> Path:
    if output_path is not None:
        return output_path
    return video_path.with_name(f"{video_path.stem}_zoomed{video_path.suffix}")


def apply_zoom(
    video_path: Path,
    output_path: Optional[Path] = None,
    sidecar_path: Optional[Path] = None,
    ffmpeg_path: str = "ffmpeg",
    dry_run: bool = False,
) -> Path:
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    out_path = resolve_output_path(video_path, Path(output_path) if output_path else None)
    sc_path = resolve_sidecar_path(video_path, Path(sidecar_path) if sidecar_path else None)

    sidecar = load_sidecar(sc_path)
    if not sidecar or not sidecar.get("events"):
        if dry_run:
            print(f"NO_ZOOM_EVENTS: copy {video_path} -> {out_path}")
            return out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if video_path.resolve() != out_path.resolve():
            shutil.copy2(video_path, out_path)
        return out_path

    viewport = sidecar.get("viewport") or {}
    width = int(viewport.get("width", 1280))
    height = int(viewport.get("height", 720))
    fps = int(sidecar.get("fps", 25))
    zoom_factor = float(sidecar.get("zoom_factor", 2.0))
    ramp_sec = float(sidecar.get("ramp_sec", 0.5))

    segments = parse_zoom_segments(
        sidecar["events"],
        zoom_factor=zoom_factor,
        ramp_sec=ramp_sec,
        fps=fps,
    )

    if not segments:
        if dry_run:
            print(f"NO_ZOOM_EVENTS: copy {video_path} -> {out_path}")
            return out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if video_path.resolve() != out_path.resolve():
            shutil.copy2(video_path, out_path)
        return out_path

    cmd = build_ffmpeg_command(
        video_path=video_path,
        output_path=out_path,
        segments=segments,
        width=width,
        height=height,
        fps=fps,
        ffmpeg_path=ffmpeg_path,
    )

    if dry_run:
        print(" ".join(shlex.quote(part) for part in cmd))
        return out_path

    out_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True)
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Apply Camtasia-style zoom post-processing via ffmpeg")
    parser.add_argument("--video", required=True, help="Path to input .webm video")
    parser.add_argument("--sidecar", default=None, help="Path to .zoom.json sidecar")
    parser.add_argument("--output", default=None, help="Path to output zoomed .webm")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="Path to ffmpeg binary")
    parser.add_argument("--dry-run", action="store_true", help="Print ffmpeg command without encoding")
    args = parser.parse_args()

    result = apply_zoom(
        video_path=Path(args.video),
        output_path=Path(args.output) if args.output else None,
        sidecar_path=Path(args.sidecar) if args.sidecar else None,
        ffmpeg_path=args.ffmpeg,
        dry_run=args.dry_run,
    )
    if not args.dry_run:
        print(str(result.resolve()))


if __name__ == "__main__":
    main()