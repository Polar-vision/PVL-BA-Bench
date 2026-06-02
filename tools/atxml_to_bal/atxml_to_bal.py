#!/usr/bin/env python3
"""Convert BlocksExchange AT.xml to BAL format."""

from __future__ import annotations

import argparse
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Intrinsics:
    focal: float
    cx: float
    cy: float
    k1: float
    k2: float
    k3: float
    p1: float
    p2: float


@dataclass(frozen=True)
class Camera:
    source_id: int
    index: int
    rotation: list[list[float]]
    center: tuple[float, float, float]


@dataclass(frozen=True)
class Point:
    xyz: tuple[float, float, float]
    observations: list[tuple[int, float, float]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Input BlocksExchange AT.xml")
    parser.add_argument("--output", required=True, type=Path, help="Output BAL file")
    parser.add_argument("--mode", choices=("normalized", "pixel"), default="normalized")
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
    principal = photogroup.find("PrincipalPoint")
    distortion = photogroup.find("Distortion")
    if principal is None or distortion is None:
        raise ValueError("Missing PrincipalPoint or Distortion")
    return Intrinsics(
        focal=ftext(photogroup, "FocalLengthPixels"),
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


def parse_cameras(photogroup: ET.Element) -> dict[int, Camera]:
    cameras: dict[int, Camera] = {}
    for index, photo in enumerate(photogroup.findall("Photo")):
        source_id = int(text(photo, "Id"))
        pose = photo.find("Pose")
        if pose is None:
            raise ValueError(f"Photo {source_id} has no Pose")
        rotation_element = pose.find("Rotation")
        center_element = pose.find("Center")
        if rotation_element is None or center_element is None:
            raise ValueError(f"Photo {source_id} has incomplete Pose")
        center = tuple(ftext(center_element, axis) for axis in ("x", "y", "z"))
        cameras[source_id] = Camera(source_id, index, parse_rotation(rotation_element), center)
    return cameras


def parse_points(block: ET.Element, cameras: dict[int, Camera], intrinsics: Intrinsics, mode: str, iterations: int) -> list[Point]:
    tiepoints = block.find("TiePoints")
    if tiepoints is None:
        raise ValueError("Missing TiePoints")
    points = []
    for tiepoint in tiepoints.findall("TiePoint"):
        position = tiepoint.find("Position")
        if position is None:
            raise ValueError("TiePoint without Position")
        xyz = tuple(ftext(position, axis) for axis in ("x", "y", "z"))
        observations = []
        for measurement in tiepoint.findall("Measurement"):
            source_id = int(text(measurement, "PhotoId"))
            u_distorted = ftext(measurement, "x")
            v_distorted = ftext(measurement, "y")
            u, v = undistort_pixel(u_distorted, v_distorted, intrinsics, iterations)
            if mode == "normalized":
                x = (u - intrinsics.cx) / intrinsics.focal
                y = (v - intrinsics.cy) / intrinsics.focal
            else:
                x = u - intrinsics.cx
                y = v - intrinsics.cy
            observations.append((cameras[source_id].index, x, y))
        observations.sort(key=lambda observation: observation)
        points.append(Point(xyz, observations))
    return points


def distort_normalized(x: float, y: float, intrinsics: Intrinsics) -> tuple[float, float]:
    r2 = x * x + y * y
    radial = 1.0 + intrinsics.k1 * r2 + intrinsics.k2 * r2 * r2 + intrinsics.k3 * r2 * r2 * r2
    xd = x * radial + 2.0 * intrinsics.p1 * x * y + intrinsics.p2 * (r2 + 2.0 * x * x)
    yd = y * radial + intrinsics.p1 * (r2 + 2.0 * y * y) + 2.0 * intrinsics.p2 * x * y
    return xd, yd


def undistort_pixel(u_distorted: float, v_distorted: float, intrinsics: Intrinsics, iterations: int) -> tuple[float, float]:
    xd = (u_distorted - intrinsics.cx) / intrinsics.focal
    yd = (v_distorted - intrinsics.cy) / intrinsics.focal
    x = xd
    y = yd
    for _ in range(iterations):
        px, py = distort_normalized(x, y, intrinsics)
        x += xd - px
        y += yd - py
    return intrinsics.focal * x + intrinsics.cx, intrinsics.focal * y + intrinsics.cy


def rotation_to_angle_axis(rotation: list[list[float]]) -> tuple[float, float, float]:
    trace = rotation[0][0] + rotation[1][1] + rotation[2][2]
    cos_theta = max(-1.0, min(1.0, (trace - 1.0) * 0.5))
    theta = math.acos(cos_theta)
    if theta < 1e-14:
        return (0.0, 0.0, 0.0)
    if abs(math.pi - theta) < 1e-6:
        # Robust enough for near-pi rotations in dataset export.
        xx = max(0.0, (rotation[0][0] + 1.0) * 0.5)
        yy = max(0.0, (rotation[1][1] + 1.0) * 0.5)
        zz = max(0.0, (rotation[2][2] + 1.0) * 0.5)
        axis = [math.sqrt(xx), math.sqrt(yy), math.sqrt(zz)]
        if rotation[0][1] < 0.0:
            axis[1] = -axis[1]
        if rotation[0][2] < 0.0:
            axis[2] = -axis[2]
        norm = math.sqrt(sum(value * value for value in axis))
        if norm == 0.0:
            return (theta, 0.0, 0.0)
        return tuple(theta * value / norm for value in axis)
    scale = theta / (2.0 * math.sin(theta))
    return (
        scale * (rotation[2][1] - rotation[1][2]),
        scale * (rotation[0][2] - rotation[2][0]),
        scale * (rotation[1][0] - rotation[0][1]),
    )


def mat_vec(matrix: list[list[float]], vector: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        sum(matrix[0][j] * vector[j] for j in range(3)),
        sum(matrix[1][j] * vector[j] for j in range(3)),
        sum(matrix[2][j] * vector[j] for j in range(3)),
    )


def translation_from_center(rotation: list[list[float]], center: tuple[float, float, float]) -> tuple[float, float, float]:
    rc = mat_vec(rotation, center)
    return (-rc[0], -rc[1], -rc[2])


def write_bal(path: Path, cameras: dict[int, Camera], points: list[Point], intrinsics: Intrinsics, mode: str) -> None:
    ordered_cameras = sorted(cameras.values(), key=lambda camera: camera.index)
    observations = [(camera_index, point_index, x, y) for point_index, point in enumerate(points) for camera_index, x, y in point.observations]
    focal = 1.0 if mode == "normalized" else intrinsics.focal
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"{len(ordered_cameras)} {len(points)} {len(observations)}\n")
        for camera_index, point_index, x, y in observations:
            fh.write(f"{camera_index} {point_index} {x:.17g} {y:.17g}\n")
        for camera in ordered_cameras:
            angle_axis = rotation_to_angle_axis(camera.rotation)
            translation = translation_from_center(camera.rotation, camera.center)
            for value in (*angle_axis, *translation, focal, 0.0, 0.0):
                fh.write(f"{value:.17g}\n")
        for point in points:
            for value in point.xyz:
                fh.write(f"{value:.17g}\n")


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
    cameras = parse_cameras(photogroup)
    points = parse_points(block, cameras, intrinsics, args.mode, args.undistort_iterations)
    write_bal(args.output, cameras, points, intrinsics, args.mode)
    observation_count = sum(len(point.observations) for point in points)
    print(f"Wrote BAL file to {args.output.resolve()}")
    print(f"  mode: {args.mode}")
    print(f"  cameras: {len(cameras)}")
    print(f"  points: {len(points)}")
    print(f"  observations: {observation_count}")


if __name__ == "__main__":
    main()
