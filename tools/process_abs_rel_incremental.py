#!/usr/bin/env python3
"""Incrementally scan and publish newly copied ABS/REL BlocksExchange datasets."""

from __future__ import annotations

import argparse
import csv
import json
import math
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
VENDOR = "武汉大势智慧科技有限公司"
SOFTWARE = "云端地球"
RMSE_LEVELS = [2, 5, 10, 20, 50, 100, 200, 500]


@dataclass
class Photo:
    source_id: int
    row_index: int
    group_id: int
    rotation: list[list[float]]
    center: tuple[float, float, float]


@dataclass
class ScanRow:
    source_xml: str
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
    missing_intrinsic_groups: int
    suspicious_intrinsic_groups: int
    status: str
    source_software_vendor: str
    source_software: str
    geometry_issue: str = ""
    issue_reason: str = ""
    control_points_raw: int = 0
    control_point_observations_raw: int = 0
    camera_z_mean: str = ""
    point_z_mean: str = ""
    mean_forward_z: str = ""
    point_z_min: str = ""
    point_z_max: str = ""


def text(parent: ET.Element, name: str) -> str:
    value = parent.findtext(name)
    if value is None:
        raise ValueError(f"Missing XML element {name}")
    return value


def ftext(parent: ET.Element, name: str) -> float:
    return float(text(parent, name))


def parse_rotation(rotation: ET.Element) -> list[list[float]]:
    return [[ftext(rotation, f"M_{i}{j}") for j in range(3)] for i in range(3)]


def has_complete_pose(photo: ET.Element) -> bool:
    pose = photo.find("Pose")
    return pose is not None and pose.find("Rotation") is not None and pose.find("Center") is not None


def remove_from_parent(stack: list[ET.Element], element: ET.Element) -> None:
    if len(stack) > 1:
        try:
            stack[-2].remove(element)
        except ValueError:
            pass
    element.clear()


def focal_length_pixels(photogroup: ET.Element, width: int, height: int) -> float | None:
    value = photogroup.findtext("FocalLengthPixels")
    if value is not None:
        return float(value)
    focal = photogroup.findtext("FocalLength")
    sensor = photogroup.findtext("SensorSize")
    if focal is None or sensor is None:
        return None
    sensor_value = float(sensor)
    if sensor_value == 0.0:
        return None
    return float(focal) / sensor_value * max(width, height)


def intrinsic_status(photogroup: ET.Element) -> tuple[bool, bool]:
    dimensions = photogroup.find("ImageDimensions")
    principal = photogroup.find("PrincipalPoint")
    distortion = photogroup.find("Distortion")
    if dimensions is None or principal is None or distortion is None:
        return True, True
    try:
        width = int(text(dimensions, "Width"))
        height = int(text(dimensions, "Height"))
        focal = focal_length_pixels(photogroup, width, height)
        cx = ftext(principal, "x")
        cy = ftext(principal, "y")
        k1 = ftext(distortion, "K1")
        k2 = ftext(distortion, "K2")
        k3 = ftext(distortion, "K3")
        p1 = ftext(distortion, "P1")
        p2 = ftext(distortion, "P2")
    except (ValueError, TypeError):
        return True, True
    if focal is None or focal <= 0.0 or width <= 0 or height <= 0:
        return True, True

    max_dim = max(width, height)
    suspicious = False
    if focal < 0.1 * max_dim or focal > 10.0 * max_dim:
        suspicious = True
    if not (-width <= cx <= 2 * width and -height <= cy <= 2 * height):
        suspicious = True
    if abs(k1) > 10.0 or abs(k2) > 100.0 or abs(k3) > 1000.0:
        suspicious = True
    if abs(p1) > 10.0 or abs(p2) > 10.0:
        suspicious = True
    return False, suspicious


def parse_photogroup(element: ET.Element, group_id: int, start_index: int) -> tuple[list[Photo], bool, bool]:
    missing, suspicious = intrinsic_status(element)
    photos: list[Photo] = []
    for photo in element.findall("Photo"):
        if not has_complete_pose(photo):
            continue
        source_id = int(text(photo, "Id"))
        pose = photo.find("Pose")
        assert pose is not None
        rotation = pose.find("Rotation")
        center = pose.find("Center")
        assert rotation is not None and center is not None
        photos.append(
            Photo(
                source_id=source_id,
                row_index=start_index + len(photos),
                group_id=group_id,
                rotation=parse_rotation(rotation),
                center=tuple(ftext(center, axis) for axis in ("x", "y", "z")),
            )
        )
    return photos, missing, suspicious


def local_forward_z(photo: Photo, point: tuple[float, float, float]) -> float:
    dx = point[0] - photo.center[0]
    dy = point[1] - photo.center[1]
    dz = point[2] - photo.center[2]
    distance = math.sqrt(dx * dx + dy * dy + dz * dz)
    if distance <= 0.0:
        return 0.0
    return dz / distance


def fmt(value: float | None) -> str:
    if value is None or math.isnan(value):
        return ""
    return f"{value:.12g}"


def scan_atxml(path: Path, area: str) -> ScanRow:
    photos: dict[int, Photo] = {}
    photogroups = 0
    missing_intrinsic_groups = 0
    suspicious_intrinsic_groups = 0
    points = 0
    observations = 0
    control_points_raw = 0
    control_point_observations_raw = 0
    observed_gcps = 0
    observed_checkpoints = 0
    gcp_observations = 0
    point_z_sum = 0.0
    point_z_min: float | None = None
    point_z_max: float | None = None
    forward_sum = 0.0
    forward_count = 0
    element_stack: list[ET.Element] = []

    for event, element in ET.iterparse(path, events=("start", "end")):
        if event == "start":
            element_stack.append(element)
            continue

        if element.tag == "Photogroup":
            photogroups += 1
            group_photos, missing, suspicious = parse_photogroup(element, photogroups, len(photos))
            missing_intrinsic_groups += int(missing)
            suspicious_intrinsic_groups += int(suspicious)
            for photo in group_photos:
                photos[photo.source_id] = photo
            remove_from_parent(element_stack, element)
        elif element.tag == "TiePoint":
            position = element.find("Position")
            if position is not None:
                xyz = tuple(ftext(position, axis) for axis in ("x", "y", "z"))
                valid_measurements: list[int] = []
                for measurement in element.findall("Measurement"):
                    source_id = int(text(measurement, "PhotoId"))
                    if source_id in photos:
                        valid_measurements.append(source_id)
                if len(valid_measurements) >= 2:
                    points += 1
                    observations += len(valid_measurements)
                    point_z_sum += xyz[2]
                    point_z_min = xyz[2] if point_z_min is None else min(point_z_min, xyz[2])
                    point_z_max = xyz[2] if point_z_max is None else max(point_z_max, xyz[2])
                    for source_id in valid_measurements:
                        forward_sum += local_forward_z(photos[source_id], xyz)
                        forward_count += 1
            remove_from_parent(element_stack, element)
        elif element.tag == "ControlPoint":
            control_points_raw += 1
            valid_observations = 0
            for measurement in element.findall("Measurement"):
                control_point_observations_raw += 1
                source_id = int(text(measurement, "PhotoId"))
                if source_id in photos:
                    valid_observations += 1
            if valid_observations:
                if element.findtext("CheckPoint", "false").strip().lower() == "true":
                    observed_checkpoints += 1
                else:
                    observed_gcps += 1
                    gcp_observations += valid_observations
            remove_from_parent(element_stack, element)
        elif element.tag == "SRS":
            remove_from_parent(element_stack, element)

        element_stack.pop()

    camera_z_mean_value = sum(photo.center[2] for photo in photos.values()) / len(photos) if photos else None
    point_z_mean_value = point_z_sum / points if points else None
    mean_forward_z_value = forward_sum / forward_count if forward_count else None
    geometry_issue = ""
    issue_reason = ""
    if mean_forward_z_value is not None and mean_forward_z_value > 0.0:
        geometry_issue = "point_cloud_above_cameras"
        reason_bits = ["mean_forward_z_is_positive"]
        if (
            camera_z_mean_value is not None
            and point_z_mean_value is not None
            and point_z_mean_value > camera_z_mean_value
        ):
            reason_bits.insert(0, "tie_points_are_above_camera_centers")
        issue_reason = "; ".join(reason_bits)

    release_gcps = 0 if area == "rel" else observed_gcps
    release_checkpoints = 0 if area == "rel" else observed_checkpoints
    release_gcp_observations = 0 if area == "rel" else gcp_observations
    source_name = path.parents[1].name
    dataset_name = (
        f"{area}-problem-i{len(photos)}-p{points}-o{observations}-"
        f"g{release_gcps}-c{release_checkpoints}"
    )
    if missing_intrinsic_groups or suspicious_intrinsic_groups:
        status = "intrinsics_issue"
    elif geometry_issue:
        status = "geometry_issue"
    else:
        status = "ok"

    return ScanRow(
        source_xml=str(path),
        area=area,
        source_name=source_name,
        dataset_name=dataset_name,
        valid_images=len(photos),
        points=points,
        observations=observations,
        gcps=release_gcps,
        checkpoints=release_checkpoints,
        gcp_observations=release_gcp_observations,
        photogroups=photogroups,
        missing_intrinsic_groups=missing_intrinsic_groups,
        suspicious_intrinsic_groups=suspicious_intrinsic_groups,
        status=status,
        source_software_vendor=VENDOR,
        source_software=SOFTWARE,
        geometry_issue=geometry_issue,
        issue_reason=issue_reason,
        control_points_raw=control_points_raw,
        control_point_observations_raw=control_point_observations_raw,
        camera_z_mean=fmt(camera_z_mean_value),
        point_z_mean=fmt(point_z_mean_value),
        mean_forward_z=fmt(mean_forward_z_value),
        point_z_min=fmt(point_z_min),
        point_z_max=fmt(point_z_max),
    )


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def scan_fieldnames(area: str) -> list[str]:
    if area == "abs":
        return [
            "source_xml",
            "area",
            "source_name",
            "dataset_name",
            "valid_images",
            "points",
            "observations",
            "gcps",
            "checkpoints",
            "gcp_observations",
            "photogroups",
            "missing_intrinsic_groups",
            "suspicious_intrinsic_groups",
            "status",
            "source_software_vendor",
            "source_software",
            "geometry_issue",
            "issue_reason",
        ]
    return [
        "source_xml",
        "area",
        "source_name",
        "dataset_name",
        "valid_images",
        "points",
        "observations",
        "gcps",
        "checkpoints",
        "gcp_observations",
        "photogroups",
        "control_points_raw",
        "control_point_observations_raw",
        "missing_intrinsic_groups",
        "suspicious_intrinsic_groups",
        "camera_z_mean",
        "point_z_mean",
        "mean_forward_z",
        "point_z_min",
        "point_z_max",
        "status",
        "geometry_issue",
        "issue_reason",
        "source_software_vendor",
        "source_software",
    ]


def manifest_fieldnames() -> list[str]:
    return [
        "source_xml",
        "area",
        "source_name",
        "dataset_name",
        "valid_images",
        "points",
        "observations",
        "gcps",
        "checkpoints",
        "gcp_observations",
        "photogroups",
        "source_software_vendor",
        "source_software",
    ]


def row_to_dict(row: ScanRow) -> dict[str, object]:
    return row.__dict__.copy()


def find_atxmls(input_root: Path, area: str) -> list[Path]:
    subdir = "ABS_AT" if area == "abs" else "REL_AT"
    paths = []
    for directory in sorted(input_root.iterdir(), key=lambda item: item.name):
        if not directory.is_dir():
            continue
        path = directory / subdir / "AT.xml"
        if path.exists():
            paths.append(path)
    return paths


def update_scan(
    area: str,
    input_root: Path,
    outputs_root: Path,
    rescan: bool = False,
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    scan_path = outputs_root / f"{area}_scan.csv"
    existing = [] if rescan else read_csv(scan_path)
    seen = {str(Path(row["source_xml"])) for row in existing}
    new_paths = [path for path in find_atxmls(input_root, area) if str(path) not in seen]
    print(f"{area}: existing_scan={len(existing)} discovered_new={len(new_paths)}", flush=True)
    new_rows: list[dict[str, object]] = []
    for index, path in enumerate(new_paths, start=1):
        print(f"{area}: scan [{index}/{len(new_paths)}] {path}", flush=True)
        try:
            new_rows.append(row_to_dict(scan_atxml(path, area)))
        except Exception as exc:  # keep batch moving and make the bad input visible in scan
            source_name = path.parents[1].name
            new_rows.append(
                {
                    "source_xml": str(path),
                    "area": area,
                    "source_name": source_name,
                    "dataset_name": f"{area}-problem-i0-p0-o0-g0-c0",
                    "valid_images": 0,
                    "points": 0,
                    "observations": 0,
                    "gcps": 0,
                    "checkpoints": 0,
                    "gcp_observations": 0,
                    "photogroups": 0,
                    "missing_intrinsic_groups": 0,
                    "suspicious_intrinsic_groups": 0,
                    "status": "scan_error",
                    "source_software_vendor": VENDOR,
                    "source_software": SOFTWARE,
                    "geometry_issue": "",
                    "issue_reason": str(exc),
                }
            )
            print(f"{area}: scan_error {path}: {exc}", flush=True)

    combined = [*existing, *new_rows]
    combined.sort(key=lambda row: str(row.get("source_name", "")))
    write_csv(scan_path, combined, scan_fieldnames(area))
    return combined, [dict(row) for row in new_rows]


def update_manifest(area: str, scan_rows: list[dict[str, str]], manifests_root: Path) -> Path:
    ok_rows = [row for row in scan_rows if row.get("status") == "ok"]
    manifest_rows = [{key: row.get(key, "") for key in manifest_fieldnames()} for row in ok_rows]
    manifest_rows.sort(key=lambda row: str(row["source_name"]))
    if area == "abs":
        manifest_path = manifests_root / f"abs_{len(manifest_rows)}.csv"
        for old_path in manifests_root.glob("abs_*.csv"):
            if old_path.name != manifest_path.name:
                old_path.unlink()
    else:
        manifest_path = manifests_root / "rel.csv"
    write_csv(manifest_path, manifest_rows, manifest_fieldnames())
    print(f"{area}: manifest={manifest_path} rows={len(manifest_rows)}", flush=True)
    return manifest_path


def run(command: list[object], cwd: Path = ROOT) -> None:
    printable = " ".join(str(item) for item in command)
    print(printable, flush=True)
    subprocess.run([str(item) for item in command], cwd=cwd, check=True)


def complete_pvl_ba(path: Path) -> bool:
    return (
        (path / "cal.txt").exists()
        and (path / "Feature.txt").exists()
        and (path / "XYZ.txt").exists()
        and any(path.glob("Cam-*.txt"))
    )


def complete_colmap(path: Path) -> bool:
    return (path / "cameras.txt").exists() and (path / "images.txt").exists() and (path / "points3D.txt").exists()


def complete_bal(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0


def originals_complete(release_root: Path, name: str) -> bool:
    return (
        complete_pvl_ba(release_root / "pvl-ba" / name / "original")
        and complete_colmap(release_root / "colmap" / name / "original")
        and complete_bal(release_root / "bal" / name / "original.bal")
    )


def write_dataset_metadata(path: Path, row: dict[str, str]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    metadata = {
        "dataset_name": row["dataset_name"],
        "source_xml": row["source_xml"],
        "area": row["area"],
        "source_name": row["source_name"],
        "images": int(row["valid_images"]),
        "points": int(row["points"]),
        "observations": int(row["observations"]),
        "gcps": int(row["gcps"]),
        "checkpoints": int(row["checkpoints"]),
        "gcp_observations": int(row["gcp_observations"]),
        "photogroups": int(row["photogroups"]),
        "source_software_vendor": row.get("source_software_vendor", VENDOR),
        "source_software": row.get("source_software", SOFTWARE),
    }
    (path / "dataset_metadata.json").write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")


def publish_originals(release_root: Path, rows: Iterable[dict[str, str]]) -> None:
    for index, row in enumerate(rows, start=1):
        name = row["dataset_name"]
        source_xml = Path(row["source_xml"])
        print(f"publish originals [{index}] {name}", flush=True)
        pvl_dir = release_root / "pvl-ba" / name / "original"
        colmap_dir = release_root / "colmap" / name / "original"
        bal_path = release_root / "bal" / name / "original.bal"
        if complete_pvl_ba(pvl_dir):
            print(f"skip existing PVL-BA original {pvl_dir}", flush=True)
        else:
            run([PYTHON, ROOT / "tools/atxml_to_pvl_ba/atxml_to_pvl_ba.py", "--input", source_xml, "--output", pvl_dir])
            write_dataset_metadata(pvl_dir, row)
        if complete_colmap(colmap_dir):
            print(f"skip existing COLMAP original {colmap_dir}", flush=True)
        else:
            run(
                [
                    PYTHON,
                    ROOT / "tools/atxml_to_colmap/atxml_to_colmap.py",
                    "--input",
                    source_xml,
                    "--output",
                    colmap_dir,
                    "--camera-model",
                    "FULL_OPENCV",
                ]
            )
            write_dataset_metadata(colmap_dir, row)
        if complete_bal(bal_path):
            print(f"skip existing BAL original {bal_path}", flush=True)
        else:
            run([PYTHON, ROOT / "tools/atxml_to_bal/atxml_to_bal.py", "--input", source_xml, "--output", bal_path, "--mode", "normalized"])
            write_dataset_metadata(bal_path.parent, row)


def pvl_base_rmse(original: Path) -> float:
    sys.path.insert(0, str(ROOT / "tools/pvl_ba_quality"))
    import generate_noisy_pvl_ba as quality  # type: ignore

    cameras = quality.read_cameras(original)
    return float(quality.rmse_for_input_stream(original, cameras).rmse)


def update_quality_plan(release_root: Path, manifest_rows: list[dict[str, str]], plan_name: str) -> Path:
    plan_path = release_root / plan_name
    existing_by_name = {row["dataset_name"]: row for row in read_csv(plan_path)}
    plan_rows: list[dict[str, object]] = []
    for row in manifest_rows:
        name = row["dataset_name"]
        if name in existing_by_name:
            plan_rows.append(existing_by_name[name])
            continue
        original = release_root / "pvl-ba" / name / "original"
        base_rmse = pvl_base_rmse(original)
        targets = [level for level in RMSE_LEVELS if level > base_rmse]
        plan_rows.append(
            {
                "dataset_name": name,
                "base_rmse_px": f"{base_rmse:.6f}",
                "points": row["points"],
                "observations": row["observations"],
                "gcps": row["gcps"],
                "targets": " ".join(str(level) for level in targets),
            }
        )
        print(f"quality plan add {name} base={base_rmse:.6f} targets={' '.join(str(t) for t in targets)}", flush=True)
    plan_rows.sort(key=lambda item: str(item["dataset_name"]))
    write_csv(plan_path, plan_rows, ["dataset_name", "base_rmse_px", "points", "observations", "gcps", "targets"])
    return plan_path


def target_dir_complete(path: Path, target: float, mode: str, tolerance: float = 0.02) -> bool:
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
        return metadata.get("mode") == mode and abs(float(metadata["actual_rmse_px"]) - target) / target <= tolerance
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def variant_dir(quality_root: Path, name: str, target: int, mode: str) -> Path:
    tail = name.replace(f"{name.split('-problem-', 1)[0]}-problem-", "").replace("-c0", "")
    tag = "init-rmse" if mode == "init-pose-triangulate" else "joint-rmse"
    return quality_root / f"{name}-{tail}-{tag}{target:03d}p00px"


def generate_quality_for_new_rows(release_root: Path, plan_path: Path, new_names: set[str], area: str) -> None:
    plan_rows = [row for row in read_csv(plan_path) if row["dataset_name"] in new_names]
    for index, row in enumerate(plan_rows, start=1):
        name = row["dataset_name"]
        targets = [int(value) for value in row["targets"].split() if value]
        if not targets:
            print(f"quality skip {name}: no targets above base RMSE", flush=True)
            continue
        original = release_root / "pvl-ba" / name / "original"
        quality_root = release_root / "pvl-ba" / name / "quality"
        quality_root.mkdir(parents=True, exist_ok=True)
        print(f"quality [{index}/{len(plan_rows)}] {name} targets={' '.join(map(str, targets))}", flush=True)
        common = [
            PYTHON,
            ROOT / "tools/pvl_ba_quality/generate_noisy_pvl_ba.py",
            "--input-dir",
            original,
            "--output-root",
            quality_root,
            "--seed",
            "20260603",
            "--prefix",
            name,
            "--rotation-weight-deg",
            "1.0",
            "--translation-weight",
            "1.0",
            "--point-weight",
            "1.0",
            "--max-scale",
            "1.0",
            "--bisection-iterations",
            "24",
            "--scale-solver",
            "sampled",
            "--sample-max-points",
            "50000",
            "--full-refine-iterations",
            "12",
            "--full-refine-tolerance",
            "0.02",
            "--full-search-steps",
            "0",
            "--error-sample-max-points",
            "100000",
            "--static-file-policy",
            "hardlink",
        ]
        main_targets = [target for target in targets if target < 200]
        stress_targets = [target for target in targets if target >= 200]
        if main_targets:
            run(common + ["--mode", "init-pose-triangulate", "--target-rmse", *[str(target) for target in main_targets]])
        if stress_targets:
            run(common + ["--mode", "init-pose-point", "--target-rmse", *[str(target) for target in stress_targets]])
        images = int(row["dataset_name"].split("-problem-i", 1)[1].split("-", 1)[0])
        stride = "1" if area == "rel" else str(max(1, math.ceil(images / 5000)))
        run(
            [
                PYTHON,
                ROOT / "tools/ba_visualizer/generate_quality_viewer.py",
                "--input-root",
                quality_root,
                "--reference-dir",
                original,
                "--output",
                release_root / "viewers" / f"{name}.html",
                "--camera-stride",
                stride,
                "--max-points",
                "100000",
                "--metric-max-points",
                "100000",
            ]
        )


def regenerate_index(release_root: Path, manifest_path: Path, area: str) -> None:
    title = "ABS Cloud Earth Viewers" if area == "abs" else "REL Cloud Earth Viewers"
    run(
        [
            PYTHON,
            ROOT / "tools/ba_visualizer/generate_viewer_index.py",
            "--viewer-dir",
            release_root / "viewers",
            "--manifest",
            manifest_path,
            "--output",
            release_root / "viewers" / "index.html",
            "--title",
            title,
        ]
    )


def manifest_path_for(area: str, manifests_root: Path) -> Path | None:
    if area == "rel":
        path = manifests_root / "rel.csv"
        return path if path.exists() else None
    matches = sorted(manifests_root.glob("abs_*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def process_area(
    area: str,
    input_root: Path,
    outputs_root: Path,
    manifests_root: Path,
    rescan: bool = False,
    resume_release: bool = False,
) -> None:
    old_manifest_path = manifest_path_for(area, manifests_root)
    old_manifest_names = {
        row["dataset_name"] for row in read_csv(old_manifest_path)
    } if old_manifest_path is not None else set()

    scan_rows, new_scan_rows = update_scan(area, input_root, outputs_root, rescan=rescan)
    manifest_path = update_manifest(area, scan_rows, manifests_root)
    manifest_rows = read_csv(manifest_path)
    if rescan:
        new_ok_names = {
            row["dataset_name"]
            for row in scan_rows
            if row.get("status") == "ok" and row["dataset_name"] not in old_manifest_names
        }
    else:
        new_ok_names = {row["dataset_name"] for row in new_scan_rows if row.get("status") == "ok"}

    release_root = outputs_root / f"{area}_release"
    if resume_release:
        planned_names = {row["dataset_name"] for row in read_csv(release_root / ("abs_quality_plan.csv" if area == "abs" else "rel_quality_plan.csv"))}
        for row in manifest_rows:
            name = row["dataset_name"]
            viewer = release_root / "viewers" / f"{name}.html"
            if (
                not originals_complete(release_root, name)
                or name not in planned_names
                or not viewer.exists()
            ):
                new_ok_names.add(name)

    print(f"{area}: new_scan_rows={len(new_scan_rows)} new_ok={len(new_ok_names)}", flush=True)
    if not new_ok_names:
        return
    new_manifest_rows = [row for row in manifest_rows if row["dataset_name"] in new_ok_names]
    publish_originals(release_root, new_manifest_rows)
    plan_path = update_quality_plan(
        release_root,
        manifest_rows,
        "abs_quality_plan.csv" if area == "abs" else "rel_quality_plan.csv",
    )
    generate_quality_for_new_rows(release_root, plan_path, new_ok_names, area)
    regenerate_index(release_root, manifest_path, area)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--abs-root", type=Path, default=Path(r"C:\zuo\Projects\ABS"))
    parser.add_argument("--rel-root", type=Path, default=Path(r"C:\zuo\Projects\REL"))
    parser.add_argument("--outputs-root", type=Path, default=ROOT / "outputs")
    parser.add_argument("--manifests-root", type=Path, default=ROOT / "manifests")
    parser.add_argument("--area", choices=("abs", "rel", "all"), default="all")
    parser.add_argument("--rescan", action="store_true", help="Rebuild scan CSV instead of scanning only unseen AT.xml files")
    parser.add_argument("--resume-release", action="store_true", help="Continue publishing rows in the current manifest that lack originals, quality plan entries, or viewers")
    args = parser.parse_args()

    if args.area in ("abs", "all"):
        process_area(
            "abs",
            args.abs_root,
            args.outputs_root,
            args.manifests_root,
            rescan=args.rescan,
            resume_release=args.resume_release,
        )
    if args.area in ("rel", "all"):
        process_area(
            "rel",
            args.rel_root,
            args.outputs_root,
            args.manifests_root,
            rescan=args.rescan,
            resume_release=args.resume_release,
        )
    print("ABS/REL incremental processing complete", flush=True)


if __name__ == "__main__":
    main()
