#!/usr/bin/env python3
"""Convert BlocksExchange AT.xml to PVL-BA format."""

from __future__ import annotations

import argparse
import math
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from pvl_ba_utils.blocksexchange import (  # noqa: E402
    has_complete_pose,
    parse_ground_control_points,
    parse_spatial_references,
    write_gcp_files,
)

@dataclass(frozen=True)
class Intrinsics:
    fx: float
    fy: float
    cx: float
    cy: float
    k1: float
    k2: float
    k3: float
    p1: float
    p2: float


@dataclass(frozen=True)
class Photo:
    source_id: int
    row_index: int
    group_id: int
    rotation: list[list[float]]
    center: tuple[float, float, float]


@dataclass(frozen=True)
class TiePoint:
    xyz: tuple[float, float, float]
    observations: list[tuple[int, float, float]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Input BlocksExchange AT.xml")
    parser.add_argument("--output", required=True, type=Path, help="Output directory")
    parser.add_argument("--undistort-iterations", type=int, default=12)
    return parser.parse_args()


def text(parent: ET.Element, name: str) -> str:
    value = parent.findtext(name)
    if value is None:
        raise ValueError(f"Missing XML element {name}")
    return value


def ftext(parent: ET.Element, name: str) -> float:
    return float(text(parent, name))


def parse_intrinsics(photogroup: ET.Element) -> Intrinsics:
    focal = ftext(photogroup, "FocalLengthPixels")
    principal = photogroup.find("PrincipalPoint")
    distortion = photogroup.find("Distortion")
    if principal is None or distortion is None:
        raise ValueError("Missing PrincipalPoint or Distortion")
    return Intrinsics(
        fx=focal,
        fy=focal,
        cx=ftext(principal, "x"),
        cy=ftext(principal, "y"),
        k1=ftext(distortion, "K1"),
        k2=ftext(distortion, "K2"),
        k3=ftext(distortion, "K3"),
        p1=ftext(distortion, "P1"),
        p2=ftext(distortion, "P2"),
    )


def parse_rotation(rotation: ET.Element) -> list[list[float]]:
    return [[ftext(rotation, f"M_{i}{j}") for j in range(3)] for i in range(3)]


def parse_photos(photogroup: ET.Element) -> dict[int, Photo]:
    photos: dict[int, Photo] = {}
    for photo in photogroup.findall("Photo"):
        source_id = int(text(photo, "Id"))
        if not has_complete_pose(photo):
            continue
        pose = photo.find("Pose")
        assert pose is not None
        rotation_element = pose.find("Rotation")
        center_element = pose.find("Center")
        assert rotation_element is not None and center_element is not None
        center = tuple(ftext(center_element, axis) for axis in ("x", "y", "z"))
        photos[source_id] = Photo(
            source_id=source_id,
            row_index=len(photos),
            group_id=1,
            rotation=parse_rotation(rotation_element),
            center=center,
        )
    return photos


def parse_tiepoints(block: ET.Element, photos: dict[int, Photo], intrinsics: Intrinsics, iterations: int) -> list[TiePoint]:
    tiepoints_element = block.find("TiePoints")
    if tiepoints_element is None:
        raise ValueError("Missing TiePoints")

    tiepoints: list[TiePoint] = []
    for tiepoint in tiepoints_element.findall("TiePoint"):
        position = tiepoint.find("Position")
        if position is None:
            raise ValueError("TiePoint without Position")
        xyz = tuple(ftext(position, axis) for axis in ("x", "y", "z"))
        observations = []
        for measurement in tiepoint.findall("Measurement"):
            source_id = int(text(measurement, "PhotoId"))
            if source_id not in photos:
                continue
            xd = ftext(measurement, "x")
            yd = ftext(measurement, "y")
            u, v = undistort_pixel(xd, yd, intrinsics, iterations)
            observations.append((photos[source_id].row_index, u, v))
        if len(observations) >= 2:
            tiepoints.append(TiePoint(xyz=xyz, observations=observations))
    return tiepoints


def distort_normalized(x: float, y: float, intrinsics: Intrinsics) -> tuple[float, float]:
    r2 = x * x + y * y
    radial = 1.0 + intrinsics.k1 * r2 + intrinsics.k2 * r2 * r2 + intrinsics.k3 * r2 * r2 * r2
    xd = x * radial + 2.0 * intrinsics.p1 * x * y + intrinsics.p2 * (r2 + 2.0 * x * x)
    yd = y * radial + intrinsics.p1 * (r2 + 2.0 * y * y) + 2.0 * intrinsics.p2 * x * y
    return xd, yd


def undistort_pixel(u_distorted: float, v_distorted: float, intrinsics: Intrinsics, iterations: int) -> tuple[float, float]:
    xd = (u_distorted - intrinsics.cx) / intrinsics.fx
    yd = (v_distorted - intrinsics.cy) / intrinsics.fy
    x = xd
    y = yd
    for _ in range(iterations):
        px, py = distort_normalized(x, y, intrinsics)
        x += xd - px
        y += yd - py
    return intrinsics.fx * x + intrinsics.cx, intrinsics.fy * y + intrinsics.cy


def euler_from_rotation(R: list[list[float]]) -> tuple[float, float, float]:
    """Invert the PVL-BA Euler matrix for ey, ex, ez."""
    # R[7] = -sin(ex)
    ex = math.asin(max(-1.0, min(1.0, -R[2][1])))
    c2 = math.cos(ex)
    if abs(c2) > 1e-12:
        ey = math.atan2(-R[2][0], R[2][2])
        ez = math.atan2(R[0][1], R[1][1])
    else:
        # Gimbal lock fallback. This dataset is far from this case, but keep output finite.
        ey = 0.0
        ez = math.atan2(-R[1][0], R[0][0])
    return ey, ex, ez


def write_cal(path: Path, intrinsics: Intrinsics) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"{intrinsics.fx:.12f} 0 {intrinsics.cx:.12f}\n")
        fh.write(f"0 {intrinsics.fy:.12f} {intrinsics.cy:.12f}\n")
        fh.write("0 0 1\n")


def write_cam(path: Path, photos: dict[int, Photo]) -> None:
    ordered = sorted(photos.values(), key=lambda photo: photo.row_index)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for photo in ordered:
            ey, ex, ez = euler_from_rotation(photo.rotation)
            cx, cy, cz = photo.center
            fh.write(
                f"{ey:.12f} {ex:.12f} {ez:.12f} "
                f"{cx:.12f} {cy:.12f} {cz:.12f} {photo.group_id}\n"
            )


def write_xyz(path: Path, tiepoints: list[TiePoint]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for tiepoint in tiepoints:
            x, y, z = tiepoint.xyz
            fh.write(f"{x:.12f} {y:.12f} {z:.12f}\n")


def write_feature(path: Path, tiepoints: list[TiePoint]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for tiepoint in tiepoints:
            fields = [str(len(tiepoint.observations))]
            for image_idx, u, v in sorted(tiepoint.observations, key=lambda observation: observation):
                fields.extend((str(image_idx), f"{u:.12f}", f"{v:.12f}"))
            fh.write(" ".join(fields))
            fh.write("\n")


def main() -> None:
    args = parse_args()
    root = ET.parse(args.input).getroot()
    block = root.find("Block")
    if block is None:
        raise ValueError("Missing Block")
    photogroup = block.find("Photogroups/Photogroup")
    if photogroup is None:
        raise ValueError("Missing Photogroup")

    intrinsics = parse_intrinsics(photogroup)
    photos = parse_photos(photogroup)
    tiepoints = parse_tiepoints(block, photos, intrinsics, args.undistort_iterations)
    references = parse_spatial_references(root)
    target_srs_id = int(text(block, "SRSId"))
    photo_index_by_source_id = {source_id: photo.row_index for source_id, photo in photos.items()}
    gcps = parse_ground_control_points(
        block,
        photo_index_by_source_id,
        references,
        target_srs_id,
        lambda x, y: undistort_pixel(x, y, intrinsics, args.undistort_iterations),
    )

    args.output.mkdir(parents=True, exist_ok=True)
    write_cal(args.output / "cal.txt", intrinsics)
    write_cam(args.output / f"Cam-{len(photos)}-.txt", photos)
    write_xyz(args.output / "XYZ.txt", tiepoints)
    write_feature(args.output / "Feature.txt", tiepoints)
    if gcps:
        write_gcp_files(args.output / "gcp.txt", args.output / "gcp_observations.txt", gcps)

    observations = sum(len(tiepoint.observations) for tiepoint in tiepoints)
    gcp_observations = sum(len(gcp.observations) for gcp in gcps)
    print(f"Wrote PVL-BA format to {args.output.resolve()}")
    print(f"  intrinsics groups: 1")
    print(f"  cameras: {len(photos)}")
    print(f"  points: {len(tiepoints)}")
    print(f"  observations: {observations}")
    print(f"  gcps: {len(gcps)}")
    print(f"  gcp observations: {gcp_observations}")
    print("  Feature.txt coordinates are undistorted pixel coordinates.")


if __name__ == "__main__":
    main()
