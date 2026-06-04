#!/usr/bin/env python3
"""Publish controlled AT blocks listed in a manifest."""

from __future__ import annotations

import argparse
import csv
import html
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


MAIN_RMSE = (2, 5, 10, 20, 50, 100)
STRESS_RMSE = (200, 500)


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
    parser.add_argument("--viewers", action="store_true", help="Generate linked quality viewers")
    parser.add_argument("--dashboard", action="store_true", help="Generate dashboard HTML for linked viewers")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of manifest rows, useful for smoke tests")
    parser.add_argument("--only", nargs="*", default=(), help="Dataset names to process")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


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


def quality_targets(preset: str) -> list[str]:
    if preset == "main":
        return [str(value) for value in MAIN_RMSE]
    if preset == "stress":
        return [str(value) for value in STRESS_RMSE]
    return [str(value) for value in (*MAIN_RMSE, *STRESS_RMSE)]


def write_dataset_metadata(output_dir: Path, row: ManifestRow) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    metadata = {
        "dataset_name": row.dataset_name,
        "source_xml": str(row.source_xml),
        "area": row.area,
        "source_name": row.source_name,
        "images": row.valid_images,
        "points": row.points,
        "observations": row.observations,
        "gcps": row.gcps,
        "checkpoints": row.checkpoints,
        "gcp_observations": row.gcp_observations,
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

    if not row.source_xml.exists():
        raise FileNotFoundError(row.source_xml)

    if "pvl-ba" in args.formats:
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
        run(
            [
                args.python,
                str(root / "tools/pvl_ba_quality/generate_noisy_pvl_ba.py"),
                "--input-dir",
                str(pvl_dir),
                "--output-root",
                str(quality_root),
                "--mode",
                "init-pose-triangulate",
                "--prefix",
                row.dataset_name,
                "--target-rmse",
                *quality_targets(args.quality_preset),
            ],
            args.dry_run,
        )

    if args.viewers:
        viewer_input = quality_root if args.quality else pvl_dir.parent
        if args.quality:
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
                    "10",
                    "--max-points",
                    "100000",
                ],
                args.dry_run,
            )
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
                    "10",
                    "--max-points",
                    "100000",
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
    rows = read_manifest((root / args.manifest).resolve() if not args.manifest.is_absolute() else args.manifest)
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


if __name__ == "__main__":
    main()
