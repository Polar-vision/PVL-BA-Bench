#!/usr/bin/env python3
"""Publish controlled AT blocks listed in a manifest."""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


MAIN_RMSE = (2, 5, 10, 20, 50, 100)
STRESS_RMSE = (200, 500)
DEFAULT_VIEWER_TARGET_FRUSTUMS = 5000
QUALITY_MODES = ("init-pose-triangulate", "init-pose-point", "observation-noise")


@dataclass(frozen=True)
class ManifestRow:
    source_xml: Path
    area: str
    source_name: str
    dataset_name: str
    valid_images: int
    points: int
    observations: int
    gcps: int
    checkpoints: int
    gcp_observations: int
    photogroups: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=Path("manifests/control_at_20.csv"))
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--formats", nargs="+", choices=("pvl-ba", "colmap", "bal"), default=("pvl-ba",))
    parser.add_argument("--quality", action="store_true", help="Generate PVL-BA quality variants")
    parser.add_argument("--quality-preset", choices=("main", "stress", "all"), default="all")
    parser.add_argument("--quality-mode", choices=QUALITY_MODES, default="init-pose-triangulate")
    parser.add_argument(
        "--quality-stress-mode",
        choices=QUALITY_MODES,
        default="init-pose-point",
        help="Generation mode used for stress RMSE levels when --quality-preset is stress or all",
    )
    parser.add_argument("--quality-seed", type=int, default=20260603)
    parser.add_argument("--rotation-weight-deg", type=float, default=1.0)
    parser.add_argument("--translation-weight", type=float, default=1.0)
    parser.add_argument("--point-weight", type=float, default=1.0)
    parser.add_argument("--max-scale", type=float, default=1.0)
    parser.add_argument("--bisection-iterations", type=int, default=24)
    parser.add_argument("--scale-solver", choices=("sampled", "exact"), default="sampled")
    parser.add_argument("--sample-max-points", type=int, default=50000)
    parser.add_argument("--full-refine-iterations", type=int, default=12)
    parser.add_argument("--full-refine-tolerance", type=float, default=0.02)
    parser.add_argument("--full-search-steps", type=int, default=0)
    parser.add_argument("--error-sample-max-points", type=int, default=100000)
    parser.add_argument("--static-file-policy", choices=("hardlink", "copy"), default="hardlink")
    parser.add_argument("--include-gcp-observations", action="store_true")
    parser.add_argument("--viewers", action="store_true", help="Generate linked quality viewers")
    parser.add_argument("--dashboard", action="store_true", help="Generate dashboard HTML for linked viewers")
    parser.add_argument(
        "--viewer-camera-stride",
        default="auto",
        help="Embed every Nth camera frustum in viewers, or 'auto' to adapt from manifest image count",
    )
    parser.add_argument(
        "--viewer-target-frustums",
        type=positive_int,
        default=DEFAULT_VIEWER_TARGET_FRUSTUMS,
        help="Target maximum camera frustums per viewer dataset when --viewer-camera-stride auto is used",
    )
    parser.add_argument("--viewer-max-points", type=int, default=100000, help="Maximum sparse points embedded in viewers")
    parser.add_argument(
        "--viewer-metric-max-points",
        type=int,
        default=100000,
        help="Maximum tie points sampled for linked-viewer metric estimates",
    )
    parser.add_argument("--limit", type=int, default=0, help="Limit number of manifest rows, useful for smoke tests")
    parser.add_argument("--only", nargs="*", default=(), help="Dataset names to process")
    parser.add_argument("--skip-existing", action="store_true", help="Skip outputs that already look complete")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    viewer_camera_stride_text = str(args.viewer_camera_stride).lower()
    if viewer_camera_stride_text == "auto":
        args.viewer_camera_stride = "auto"
    else:
        args.viewer_camera_stride = positive_int(str(args.viewer_camera_stride))
    return args


def positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {value!r}") from exc
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"expected a positive integer, got {value!r}")
    return parsed


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def read_manifest(path: Path) -> list[ManifestRow]:
    rows = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for raw in csv.DictReader(fh):
            rows.append(
                ManifestRow(
                    source_xml=Path(raw["source_xml"]),
                    area=raw["area"],
                    source_name=raw["source_name"],
                    dataset_name=raw["dataset_name"],
                    valid_images=int(raw["valid_images"]),
                    points=int(raw["points"]),
                    observations=int(raw["observations"]),
                    gcps=int(raw["gcps"]),
                    checkpoints=int(raw["checkpoints"]),
                    gcp_observations=int(raw["gcp_observations"]),
                    photogroups=int(raw["photogroups"]),
                )
            )
    return rows


def run(command: list[str], dry_run: bool) -> None:
    printable = " ".join(f'"{item}"' if " " in item else item for item in command)
    print(printable, flush=True)
    if not dry_run:
        subprocess.run(command, check=True)


def quality_jobs(args: argparse.Namespace) -> list[tuple[str, list[str]]]:
    jobs: list[tuple[str, list[str]]] = []
    if args.quality_preset in ("main", "all"):
        jobs.append((args.quality_mode, [str(value) for value in MAIN_RMSE]))
    if args.quality_preset in ("stress", "all"):
        jobs.append((args.quality_stress_mode, [str(value) for value in STRESS_RMSE]))
    merged: list[tuple[str, list[str]]] = []
    for mode, targets in jobs:
        if merged and merged[-1][0] == mode:
            merged[-1][1].extend(targets)
        else:
            merged.append((mode, list(targets)))
    return merged


def pvl_ba_complete(path: Path) -> bool:
    return (
        (path / "cal.txt").exists()
        and (path / "Feature.txt").exists()
        and (path / "XYZ.txt").exists()
        and any(path.glob("Cam-*.txt"))
    )


def colmap_complete(path: Path) -> bool:
    return (path / "cameras.txt").exists() and (path / "images.txt").exists() and (path / "points3D.txt").exists()


def bal_complete(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def quality_rmse_tag(target: str) -> str:
    return f"{float(target):06.2f}".replace(".", "p")


def target_relative_error(actual_rmse: float, target_rmse: float) -> float:
    return abs(actual_rmse - target_rmse) / target_rmse if target_rmse > 0.0 else 0.0


def quality_variant_complete(path: Path, target: str, tolerance: float, mode: str) -> bool:
    metadata_path = path / "noise_metadata.json"
    if not (
        (path / "cal.txt").exists()
        and (path / "Feature.txt").exists()
        and (path / "XYZ.txt").exists()
        and metadata_path.exists()
        and any(path.glob("Cam-*.txt"))
    ):
        return False
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if metadata.get("mode") != mode:
            return False
        actual_rmse = float(metadata["actual_rmse_px"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return target_relative_error(actual_rmse, float(target)) <= tolerance


def quality_tag(mode: str) -> str:
    if mode == "init-pose-triangulate":
        return "init-rmse"
    if mode == "init-pose-point":
        return "joint-rmse"
    if mode == "observation-noise":
        return "obs-rmse"
    raise ValueError(f"Unsupported quality mode: {mode}")


def quality_complete(path: Path, mode: str, targets: list[str], tolerance: float) -> bool:
    tag = quality_tag(mode)
    for target in targets:
        pattern = f"*-{tag}{quality_rmse_tag(target)}px"
        if not any(quality_variant_complete(candidate, target, tolerance, mode) for candidate in path.glob(pattern)):
            return False
    return True


def viewer_camera_stride(args: argparse.Namespace, row: ManifestRow) -> int:
    if args.viewer_camera_stride == "auto":
        stride = max(1, math.ceil(row.valid_images / args.viewer_target_frustums))
        print(
            f"viewer camera stride auto -> {stride} "
            f"for {row.dataset_name} ({row.valid_images} images, target {args.viewer_target_frustums})",
            flush=True,
        )
        return stride
    return args.viewer_camera_stride


def gcp_sidecar_stats(output_dir: Path) -> tuple[int, int, int] | None:
    checkpoints_by_id = {}
    gcp_candidates = [output_dir / "gcp.txt", *sorted(output_dir.glob("*.gcp.txt"))]
    for gcp_path in gcp_candidates:
        if not gcp_path.exists():
            continue
        for line in gcp_path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            tokens = line.split()
            checkpoints_by_id[int(tokens[0])] = int(tokens[7])
        break

    observation_candidates = [output_dir / "gcp_observations.txt", *sorted(output_dir.glob("*.gcp_observations.txt"))]
    for observations_path in observation_candidates:
        if not observations_path.exists():
            continue
        gcp_count = 0
        checkpoint_count = 0
        observation_count = 0
        for line in observations_path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            tokens = line.split()
            gcp_id = int(tokens[0])
            track_len = int(tokens[1])
            if track_len > 0:
                gcp_count += 1
                checkpoint_count += checkpoints_by_id.get(gcp_id, 0)
            observation_count += track_len
        return gcp_count, checkpoint_count, observation_count

    if checkpoints_by_id:
        return len(checkpoints_by_id), sum(checkpoints_by_id.values()), 0
    return None


def write_dataset_metadata(output_dir: Path, row: ManifestRow) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    gcp_stats = gcp_sidecar_stats(output_dir)
    gcp_count, checkpoint_count, gcp_observation_count = gcp_stats if gcp_stats is not None else (
        (0, 0, 0) if row.gcp_observations == 0 else (row.gcps, row.checkpoints, row.gcp_observations)
    )
    metadata = {
        "dataset_name": row.dataset_name,
        "source_xml": str(row.source_xml),
        "area": row.area,
        "source_name": row.source_name,
        "images": row.valid_images,
        "points": row.points,
        "observations": row.observations,
        "gcps": gcp_count,
        "checkpoints": checkpoint_count,
        "gcp_observations": gcp_observation_count,
        "photogroups": row.photogroups,
    }
    (output_dir / "dataset_metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")


def process_row(args: argparse.Namespace, row: ManifestRow, root: Path) -> dict:
    output_root = args.output_root.resolve()
    pvl_dir = output_root / "pvl-ba" / row.dataset_name / "original"
    colmap_dir = output_root / "colmap" / row.dataset_name / "original"
    bal_path = output_root / "bal" / row.dataset_name / "original.bal"
    quality_root = output_root / "pvl-ba" / row.dataset_name / "quality"
    viewer_path = output_root / "viewers" / f"{row.dataset_name}.html"
    quality_changed = False

    if not row.source_xml.exists():
        raise FileNotFoundError(row.source_xml)

    if "pvl-ba" in args.formats:
        if args.skip_existing and pvl_ba_complete(pvl_dir):
            print(f"skip existing PVL-BA original {pvl_dir}", flush=True)
        else:
            run(
                [
                    args.python,
                    str(root / "tools/atxml_to_pvl_ba/atxml_to_pvl_ba.py"),
                    "--input",
                    str(row.source_xml),
                    "--output",
                    str(pvl_dir),
                ],
                args.dry_run,
            )
            if not args.dry_run:
                write_dataset_metadata(pvl_dir, row)

    if "colmap" in args.formats:
        if args.skip_existing and colmap_complete(colmap_dir):
            print(f"skip existing COLMAP original {colmap_dir}", flush=True)
        else:
            run(
                [
                    args.python,
                    str(root / "tools/atxml_to_colmap/atxml_to_colmap.py"),
                    "--input",
                    str(row.source_xml),
                    "--output",
                    str(colmap_dir),
                    "--camera-model",
                    "FULL_OPENCV",
                ],
                args.dry_run,
            )
            if not args.dry_run:
                write_dataset_metadata(colmap_dir, row)

    if "bal" in args.formats:
        if args.skip_existing and bal_complete(bal_path):
            print(f"skip existing BAL original {bal_path}", flush=True)
        else:
            run(
                [
                    args.python,
                    str(root / "tools/atxml_to_bal/atxml_to_bal.py"),
                    "--input",
                    str(row.source_xml),
                    "--output",
                    str(bal_path),
                    "--mode",
                    "normalized",
                ],
                args.dry_run,
            )
            if not args.dry_run:
                write_dataset_metadata(bal_path.parent, row)

    if args.quality:
        if "pvl-ba" not in args.formats and not pvl_dir.exists():
            raise FileNotFoundError(f"PVL-BA original not found for quality generation: {pvl_dir}")
        for mode, targets in quality_jobs(args):
            if args.skip_existing and quality_complete(quality_root, mode, targets, args.full_refine_tolerance):
                print(f"skip existing {mode} quality variants {quality_root}", flush=True)
                continue
            run(
                [
                    args.python,
                    str(root / "tools/pvl_ba_quality/generate_noisy_pvl_ba.py"),
                    "--input-dir",
                    str(pvl_dir),
                    "--output-root",
                    str(quality_root),
                    "--seed",
                    str(args.quality_seed),
                    "--mode",
                    mode,
                    "--prefix",
                    row.dataset_name,
                    "--rotation-weight-deg",
                    str(args.rotation_weight_deg),
                    "--translation-weight",
                    str(args.translation_weight),
                    "--point-weight",
                    str(args.point_weight),
                    "--max-scale",
                    str(args.max_scale),
                    "--bisection-iterations",
                    str(args.bisection_iterations),
                    "--scale-solver",
                    args.scale_solver,
                    "--sample-max-points",
                    str(args.sample_max_points),
                    "--full-refine-iterations",
                    str(args.full_refine_iterations),
                    "--full-refine-tolerance",
                    str(args.full_refine_tolerance),
                    "--full-search-steps",
                    str(args.full_search_steps),
                    "--error-sample-max-points",
                    str(args.error_sample_max_points),
                    "--static-file-policy",
                    args.static_file_policy,
                    "--target-rmse",
                    *targets,
                ]
                + (["--include-gcp-observations"] if args.include_gcp_observations else []),
                args.dry_run,
            )
            quality_changed = True

    if args.viewers:
        viewer_input = quality_root if args.quality else pvl_dir.parent
        camera_stride = viewer_camera_stride(args, row)
        if args.quality:
            if args.skip_existing and viewer_path.exists() and not quality_changed:
                print(f"skip existing viewer {viewer_path}", flush=True)
            else:
                run(
                    [
                        args.python,
                        str(root / "tools/ba_visualizer/generate_quality_viewer.py"),
                        "--input-root",
                        str(viewer_input),
                        "--reference-dir",
                        str(pvl_dir),
                        "--output",
                        str(viewer_path),
                        "--camera-stride",
                        str(camera_stride),
                        "--max-points",
                        str(args.viewer_max_points),
                        "--metric-max-points",
                        str(args.viewer_metric_max_points),
                    ],
                    args.dry_run,
                )
        else:
            if args.skip_existing and viewer_path.exists():
                print(f"skip existing viewer {viewer_path}", flush=True)
            else:
                run(
                    [
                        args.python,
                        str(root / "tools/ba_visualizer/generate_viewer.py"),
                        "--input-dir",
                        str(pvl_dir),
                        "--output",
                        str(viewer_path),
                        "--format",
                        "pvl-ba",
                        "--camera-stride",
                        str(camera_stride),
                        "--max-points",
                        str(args.viewer_max_points),
                    ],
                    args.dry_run,
                )

    return {"name": row.dataset_name, "area": row.area, "viewer": viewer_path}


def write_dashboard(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    options = "\n".join(
        f'<option value="{html.escape(os.path.relpath(entry["viewer"].resolve(), path.parent.resolve()).replace(os.sep, "/"))}">'
        f'{html.escape(entry["name"])}</option>'
        for entry in entries
    )
    first = html.escape(os.path.relpath(entries[0]["viewer"].resolve(), path.parent.resolve()).replace(os.sep, "/")) if entries else ""
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PVL-BA Control AT Dashboard</title>
  <style>
    html, body {{ margin: 0; height: 100%; overflow: hidden; font-family: Segoe UI, Arial, sans-serif; background: #101215; color: #eef2f6; }}
    header {{ height: 48px; display: flex; align-items: center; gap: 12px; padding: 0 14px; background: #181d23; border-bottom: 1px solid rgba(255,255,255,0.12); }}
    select {{ max-width: min(760px, calc(100vw - 220px)); height: 32px; background: #232a32; color: #eef2f6; border: 1px solid rgba(255,255,255,0.18); border-radius: 6px; padding: 0 8px; }}
    iframe {{ width: 100%; height: calc(100% - 49px); border: 0; display: block; }}
  </style>
</head>
<body>
  <header>
    <strong>PVL-BA Control AT Dashboard</strong>
    <select id="dataset">{options}</select>
  </header>
  <iframe id="viewer" src="{first}"></iframe>
  <script>
    const select = document.getElementById('dataset');
    const viewer = document.getElementById('viewer');
    select.addEventListener('change', () => viewer.src = select.value);
  </script>
</body>
</html>
"""
    path.write_text(page, encoding="utf-8", newline="\n")


def main() -> None:
    args = parse_args()
    root = repo_root()
    manifest_path = (root / args.manifest).resolve() if not args.manifest.is_absolute() else args.manifest
    rows = read_manifest(manifest_path)
    if args.only:
        allowed = set(args.only)
        rows = [row for row in rows if row.dataset_name in allowed]
    if args.limit:
        rows = rows[: args.limit]
    entries = []
    for index, row in enumerate(rows, start=1):
        print(f"[{index}/{len(rows)}] {row.dataset_name}", flush=True)
        entries.append(process_row(args, row, root))
    if args.dashboard:
        dashboard_path = args.output_root.resolve() / "dashboard.html"
        print(f"write dashboard {dashboard_path}", flush=True)
        if not args.dry_run and entries:
            write_dashboard(dashboard_path, entries)
        viewer_index_path = args.output_root.resolve() / "viewers" / "index.html"
        print(f"write viewer index {viewer_index_path}", flush=True)
        if entries:
            run(
                [
                    args.python,
                    str(root / "tools/ba_visualizer/generate_viewer_index.py"),
                    "--viewer-dir",
                    str(viewer_index_path.parent),
                    "--output",
                    str(viewer_index_path),
                    "--manifest",
                    str(manifest_path),
                ],
                args.dry_run,
            )


if __name__ == "__main__":
    main()
