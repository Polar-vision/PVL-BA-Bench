#!/usr/bin/env python3
"""Publish missing BAL originals for BA-COLMAP release rows."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=ROOT / "manifests/ba_colmap_122.csv")
    parser.add_argument("--release-root", type=Path, default=ROOT / "outputs/ba_colmap_release")
    parser.add_argument("--skip-existing", action="store_true")
    parser.add_argument("--python", default=sys.executable)
    return parser.parse_args()


def read_manifest(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def bal_complete(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def write_dataset_metadata(path: Path, row: dict[str, str]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    metadata = {
        "dataset_name": row["dataset_name"],
        "source_dir": row["source_dir"],
        "area": row["area"],
        "source_name": row["source_name"],
        "images": int(row["valid_images"]),
        "points": int(row["points"]),
        "observations": int(row["observations"]),
        "gcps": int(row["gcps"]),
        "checkpoints": int(row["checkpoints"]),
        "gcp_observations": int(row["gcp_observations"]),
        "cameras": int(row["cameras"]),
        "photogroups": int(row["photogroups"]),
        "camera_models": row["camera_models"],
        "source_format": row["source_format"],
        "source_software": row["source_software"],
        "bal_observation_mode": "normalized_undistorted",
    }
    (path / "dataset_metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")


def run(command: list[object]) -> None:
    printable = " ".join(str(item) for item in command)
    print(printable, flush=True)
    subprocess.run([str(item) for item in command], cwd=ROOT, check=True)


def main() -> None:
    args = parse_args()
    rows = read_manifest(args.manifest)
    for index, row in enumerate(rows, start=1):
        name = row["dataset_name"]
        input_dir = args.release_root / "colmap" / name / "original"
        bal_path = args.release_root / "bal" / name / "original.bal"
        print(f"ba-colmap BAL [{index}/{len(rows)}] {name}", flush=True)
        if args.skip_existing and bal_complete(bal_path):
            print(f"skip existing BAL original {bal_path}", flush=True)
            continue
        if not input_dir.exists():
            raise FileNotFoundError(input_dir)
        run(
            [
                args.python,
                ROOT / "tools/colmap_to_bal/colmap_to_bal.py",
                "--input",
                input_dir,
                "--output",
                bal_path,
            ]
        )
        write_dataset_metadata(bal_path.parent, row)
    print("BA-COLMAP BAL publish complete", flush=True)


if __name__ == "__main__":
    main()
