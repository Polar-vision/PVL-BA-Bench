#!/usr/bin/env python3
"""Convert BlocksExchange AT.xml to COLMAP text model files."""

from __future__ import annotations

import argparse
import math
import os
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

sys.path.append(str(Path(__file__).resolve().parents[1]))
from pvl_ba_utils.blocksexchange import (  # noqa: E402
    has_complete_pose,
    parse_ground_control_points,
    parse_spatial_references,
    write_gcp_files,
)

@dataclass(frozen=True)
class Camera:
    camera_id: int
    model: str
    width: int
    height: int
    params: tuple[float, ...]


@dataclass(frozen=True)
class Image:
    image_id: int
    qvec: tuple[float, float, float, float]
    tvec: tuple[float, float, float]
    camera_id: int
    name: str


@dataclass
class Point3D:
    point3d_id: int
    xyz: tuple[float, float, float]
    rgb: tuple[int, int, int]
    observations: list[tuple[int, float, float]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Input BlocksExchange AT.xml")
    parser.add_argument("--output", required=True, type=Path, help="Output COLMAP text model directory")
    parser.add_argument(
        "--camera-model",
        choices=("OPENCV", "FULL_OPENCV"),
        default="OPENCV",
        help="COLMAP camera model to write. OPENCV drops XML K3; FULL_OPENCV preserves it.",
    )
    return parser.parse_args()


def text(parent: ET.Element, name: str) -> str:
    value = parent.findtext(name)
    if value is None:
        raise ValueError(f"Missing XML element: {name}")
    return value


def ftext(parent: ET.Element, name: str) -> float:
    return float(text(parent, name))


def matrix_from_rotation(rotation: ET.Element) -> list[list[float]]:
    return [[ftext(rotation, f"M_{i}{j}") for j in range(3)] for i in range(3)]


def mat_vec(matrix: list[list[float]], vector: Iterable[float]) -> tuple[float, float, float]:
    v = tuple(vector)
    return (
        sum(matrix[0][j] * v[j] for j in range(3)),
        sum(matrix[1][j] * v[j] for j in range(3)),
        sum(matrix[2][j] * v[j] for j in range(3)),
    )


def rotation_matrix_to_qvec(matrix: list[list[float]]) -> tuple[float, float, float, float]:
    """Return COLMAP qvec in qw, qx, qy, qz order."""
    m00, m01, m02 = matrix[0]
    m10, m11, m12 = matrix[1]
    m20, m21, m22 = matrix[2]
    trace = m00 + m11 + m22

    if trace > 0.0:
        s = math.sqrt(trace + 1.0) * 2.0
        qw = 0.25 * s
        qx = (m21 - m12) / s
        qy = (m02 - m20) / s
        qz = (m10 - m01) / s
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(1.0 + m00 - m11 - m22) * 2.0
        qw = (m21 - m12) / s
        qx = 0.25 * s
        qy = (m01 + m10) / s
        qz = (m02 + m20) / s
    elif m11 > m22:
        s = math.sqrt(1.0 + m11 - m00 - m22) * 2.0
        qw = (m02 - m20) / s
        qx = (m01 + m10) / s
        qy = 0.25 * s
        qz = (m12 + m21) / s
    else:
        s = math.sqrt(1.0 + m22 - m00 - m11) * 2.0
        qw = (m10 - m01) / s
        qx = (m02 + m20) / s
        qy = (m12 + m21) / s
        qz = 0.25 * s

    norm = math.sqrt(qw * qw + qx * qx + qy * qy + qz * qz)
    return (qw / norm, qx / norm, qy / norm, qz / norm)


def image_name_from_path(image_path: str) -> str:
    normalized = image_path.replace("\\", "/")
    return normalized.rsplit("/", 1)[-1]


def color_to_rgb(color: ET.Element | None) -> tuple[int, int, int]:
    if color is None:
        return (0, 0, 0)
    channels = []
    for name in ("Red", "Green", "Blue"):
        value = float(text(color, name))
        if value <= 1.0:
            value *= 255.0
        channels.append(max(0, min(255, int(round(value)))))
    return tuple(channels)  # type: ignore[return-value]


def parse_camera(photogroup: ET.Element, camera_model: str) -> Camera:
    dimensions = photogroup.find("ImageDimensions")
    if dimensions is None:
        raise ValueError("Missing ImageDimensions")
    width = int(text(dimensions, "Width"))
    height = int(text(dimensions, "Height"))
    focal = ftext(photogroup, "FocalLengthPixels")

    principal = photogroup.find("PrincipalPoint")
    if principal is None:
        raise ValueError("Missing PrincipalPoint")
    cx = ftext(principal, "x")
    cy = ftext(principal, "y")

    distortion = photogroup.find("Distortion")
    if distortion is None:
        raise ValueError("Missing Distortion")
    k1 = ftext(distortion, "K1")
    k2 = ftext(distortion, "K2")
    k3 = ftext(distortion, "K3")
    p1 = ftext(distortion, "P1")
    p2 = ftext(distortion, "P2")

    if camera_model == "OPENCV":
        params = (focal, focal, cx, cy, k1, k2, p1, p2)
    else:
        params = (focal, focal, cx, cy, k1, k2, k3, 0.0, 0.0, 0.0, p1, p2)
    return Camera(1, camera_model, width, height, params)


def parse_images(photogroup: ET.Element) -> dict[int, Image]:
    images: dict[int, Image] = {}
    next_image_id = 1
    for photo in photogroup.findall("Photo"):
        source_id = int(text(photo, "Id"))
        if not has_complete_pose(photo):
            continue
        image_path = text(photo, "ImagePath")
        pose = photo.find("Pose")
        assert pose is not None
        rotation_element = pose.find("Rotation")
        center_element = pose.find("Center")
        assert rotation_element is not None and center_element is not None

        rotation = matrix_from_rotation(rotation_element)
        center = tuple(ftext(center_element, axis) for axis in ("x", "y", "z"))
        qvec = rotation_matrix_to_qvec(rotation)
        rc = mat_vec(rotation, center)
        tvec = (-rc[0], -rc[1], -rc[2])
        image_id = next_image_id
        next_image_id += 1
        images[image_id] = Image(image_id, qvec, tvec, 1, image_name_from_path(image_path))
    return images


def parse_points(block: ET.Element, image_id_by_source_id: dict[int, int]) -> list[Point3D]:
    tiepoints = block.find("TiePoints")
    if tiepoints is None:
        raise ValueError("Missing TiePoints")

    points: list[Point3D] = []
    for tiepoint in tiepoints.findall("TiePoint"):
        position = tiepoint.find("Position")
        if position is None:
            raise ValueError("TiePoint has no Position")
        xyz = tuple(ftext(position, axis) for axis in ("x", "y", "z"))
        rgb = color_to_rgb(tiepoint.find("Color"))
        observations = []
        for measurement in tiepoint.findall("Measurement"):
            source_id = int(text(measurement, "PhotoId"))
            image_id = image_id_by_source_id.get(source_id)
            if image_id is None:
                continue
            observations.append(
                (
                    image_id,
                    ftext(measurement, "x"),
                    ftext(measurement, "y"),
                )
            )
        if len(observations) >= 2:
            points.append(Point3D(len(points) + 1, xyz, rgb, observations))
    return points


def write_cameras(path: Path, camera: Camera) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# Camera list with one line of data per camera:\n")
        fh.write("#   CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]\n")
        fh.write("# Number of cameras: 1\n")
        params = " ".join(format_float(value) for value in camera.params)
        fh.write(f"{camera.camera_id} {camera.model} {camera.width} {camera.height} {params}\n")


def write_images(path: Path, images: dict[int, Image], image_points: dict[int, list[tuple[float, float, int]]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# Image list with two lines of data per image:\n")
        fh.write("#   IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, NAME\n")
        fh.write("#   POINTS2D[] as (X, Y, POINT3D_ID)\n")
        fh.write(f"# Number of images: {len(images)}\n")
        for image_id in sorted(images):
            image = images[image_id]
            q = " ".join(format_float(value) for value in image.qvec)
            t = " ".join(format_float(value) for value in image.tvec)
            fh.write(f"{image.image_id} {q} {t} {image.camera_id} {image.name}\n")
            points = image_points.get(image_id, [])
            fh.write(" ".join(f"{format_float(x)} {format_float(y)} {pid}" for x, y, pid in points))
            fh.write("\n")


def write_points(path: Path, points: list[Point3D], image_point_indices: dict[tuple[int, int], int]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# 3D point list with one line of data per point:\n")
        fh.write("#   POINT3D_ID, X, Y, Z, R, G, B, ERROR, TRACK[] as (IMAGE_ID, POINT2D_IDX)\n")
        fh.write(f"# Number of points: {len(points)}\n")
        for point in points:
            xyz = " ".join(format_float(value) for value in point.xyz)
            r, g, b = point.rgb
            track_items = []
            for image_id, _x, _y in point.observations:
                idx = image_point_indices[(image_id, point.point3d_id)]
                track_items.append(f"{image_id} {idx}")
            track = " ".join(track_items)
            fh.write(f"{point.point3d_id} {xyz} {r} {g} {b} 0 {track}\n")


def format_float(value: float) -> str:
    return f"{value:.17g}"


def build_image_points(points: list[Point3D]) -> tuple[dict[int, list[tuple[float, float, int]]], dict[tuple[int, int], int]]:
    image_points: dict[int, list[tuple[float, float, int]]] = {}
    image_point_indices: dict[tuple[int, int], int] = {}
    for point in points:
        for image_id, x, y in point.observations:
            values = image_points.setdefault(image_id, [])
            image_point_indices[(image_id, point.point3d_id)] = len(values)
            values.append((x, y, point.point3d_id))
    return image_points, image_point_indices


def parse_image_id_by_source_id(photogroup: ET.Element) -> dict[int, int]:
    mapping: dict[int, int] = {}
    for photo in photogroup.findall("Photo"):
        source_id = int(text(photo, "Id"))
        if has_complete_pose(photo):
            mapping[source_id] = len(mapping) + 1
    return mapping


def main() -> None:
    args = parse_args()
    input_path = args.input.resolve()
    output_dir = args.output.resolve()
    if not input_path.exists():
        raise FileNotFoundError(input_path)

    tree = ET.parse(input_path)
    root = tree.getroot()
    block = root.find("Block")
    if block is None:
        raise ValueError("Missing Block element")
    photogroup = block.find("Photogroups/Photogroup")
    if photogroup is None:
        raise ValueError("Missing Photogroup element")

    camera = parse_camera(photogroup, args.camera_model)
    images = parse_images(photogroup)
    image_id_by_source_id = parse_image_id_by_source_id(photogroup)
    points = parse_points(block, image_id_by_source_id)
    image_points, image_point_indices = build_image_points(points)
    references = parse_spatial_references(root)
    target_srs_id = int(text(block, "SRSId"))
    gcps = parse_ground_control_points(
        block,
        image_id_by_source_id,
        references,
        target_srs_id,
        lambda x, y: (x, y),
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    write_cameras(output_dir / "cameras.txt", camera)
    write_images(output_dir / "images.txt", images, image_points)
    write_points(output_dir / "points3D.txt", points, image_point_indices)
    if gcps:
        write_gcp_files(output_dir / "gcp.txt", output_dir / "gcp_observations.txt", gcps)

    measurement_count = sum(len(point.observations) for point in points)
    gcp_observations = sum(len(gcp.observations) for gcp in gcps)
    print(f"Wrote COLMAP text model to {output_dir}")
    print(f"  cameras: 1")
    print(f"  images: {len(images)}")
    print(f"  points3D: {len(points)}")
    print(f"  observations: {measurement_count}")
    print(f"  gcps: {len(gcps)}")
    print(f"  gcp observations: {gcp_observations}")
    if args.camera_model == "OPENCV":
        print("  note: XML K3 was dropped; use --camera-model FULL_OPENCV to keep it.")


if __name__ == "__main__":
    main()
