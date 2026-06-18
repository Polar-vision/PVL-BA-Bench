#!/usr/bin/env python3
"""Convert BlocksExchange AT.xml to COLMAP text model files."""

from __future__ import annotations

import argparse
import math
import os
import shutil
import sys
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

sys.path.append(str(Path(__file__).resolve().parents[1]))
from pvl_ba_utils.blocksexchange import (  # noqa: E402
    SpatialReference,
    control_point_to_gcp,
    has_complete_pose,
    observed_ground_control_points,
    remove_from_parent,
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


class ImagePointTempWriter:
    def __init__(self, root: Path, max_open_files: int = 128) -> None:
        self.root = root
        self.max_open_files = max_open_files
        self.handles: OrderedDict[int, object] = OrderedDict()

    def path_for_image(self, image_id: int) -> Path:
        shard = self.root / f"{image_id // 1000:06d}"
        return shard / f"{image_id}.txt"

    def handle_for_image(self, image_id: int):
        handle = self.handles.get(image_id)
        if handle is not None:
            self.handles.move_to_end(image_id)
            return handle
        path = self.path_for_image(image_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a", encoding="utf-8", newline="\n")
        self.handles[image_id] = handle
        if len(self.handles) > self.max_open_files:
            _old_image_id, old_handle = self.handles.popitem(last=False)
            old_handle.close()
        return handle

    def write(self, image_id: int, x: float, y: float, point3d_id: int) -> None:
        handle = self.handle_for_image(image_id)
        handle.write(f"{format_float(x)} {format_float(y)} {point3d_id} ")

    def close(self) -> None:
        for handle in self.handles.values():
            handle.close()
        self.handles.clear()


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


def focal_length_pixels(photogroup: ET.Element, width: int, height: int) -> float:
    value = photogroup.findtext("FocalLengthPixels")
    if value is not None:
        return float(value)
    sensor_size = ftext(photogroup, "SensorSize")
    if sensor_size == 0.0:
        raise ValueError("SensorSize must be nonzero when FocalLengthPixels is missing")
    return ftext(photogroup, "FocalLength") / sensor_size * max(width, height)


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


def parse_camera(photogroup: ET.Element, camera_model: str, camera_id: int) -> Camera:
    dimensions = photogroup.find("ImageDimensions")
    if dimensions is None:
        raise ValueError("Missing ImageDimensions")
    width = int(text(dimensions, "Width"))
    height = int(text(dimensions, "Height"))
    focal = focal_length_pixels(photogroup, width, height)

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
    return Camera(camera_id, camera_model, width, height, params)


def parse_images(photogroup: ET.Element, camera_id: int, start_image_id: int) -> dict[int, Image]:
    images: dict[int, Image] = {}
    next_image_id = start_image_id
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
        images[image_id] = Image(image_id, qvec, tvec, camera_id, image_name_from_path(image_path))
    return images


def parse_photogroups(block: ET.Element, camera_model: str) -> tuple[dict[int, Camera], dict[int, Image], dict[int, int]]:
    photogroups = block.findall("Photogroups/Photogroup")
    if not photogroups:
        raise ValueError("Missing Photogroup element")
    cameras: dict[int, Camera] = {}
    images: dict[int, Image] = {}
    image_id_by_source_id: dict[int, int] = {}
    for camera_id, photogroup in enumerate(photogroups, start=1):
        cameras[camera_id] = parse_camera(photogroup, camera_model, camera_id)
        group_images = parse_images(photogroup, camera_id, len(images) + 1)
        for image_id, image in group_images.items():
            images[image_id] = image
        for photo in photogroup.findall("Photo"):
            source_id = int(text(photo, "Id"))
            if has_complete_pose(photo):
                if source_id in image_id_by_source_id:
                    raise ValueError(f"Duplicate Photo Id across photogroups: {source_id}")
                image_id_by_source_id[source_id] = len(image_id_by_source_id) + 1
    return cameras, images, image_id_by_source_id


def stream_metadata(
    input_path: Path,
    camera_model: str,
) -> tuple[dict[int, Camera], dict[int, Image], dict[int, int], dict[int, SpatialReference], int]:
    cameras: dict[int, Camera] = {}
    images: dict[int, Image] = {}
    image_id_by_source_id: dict[int, int] = {}
    references: dict[int, SpatialReference] = {}
    target_srs_id = 0
    element_stack: list[ET.Element] = []
    tag_stack: list[str] = []

    for event, element in ET.iterparse(input_path, events=("start", "end")):
        if event == "start":
            element_stack.append(element)
            tag_stack.append(element.tag)
            continue

        path = tuple(tag_stack)
        if element.tag == "SRSId" and path[-3:] == ("BlocksExchange", "Block", "SRSId"):
            target_srs_id = int(element.text or "0")
        elif element.tag == "SRS" and len(path) >= 3 and path[-2] == "SpatialReferenceSystems":
            srs_id = int(text(element, "Id"))
            references[srs_id] = SpatialReference(
                srs_id=srs_id,
                name=element.findtext("Name", ""),
                definition=element.findtext("Definition", ""),
            )
            remove_from_parent(element_stack, element)
        elif element.tag == "Photogroup":
            camera_id = len(cameras) + 1
            cameras[camera_id] = parse_camera(element, camera_model, camera_id)
            start_image_id = len(images) + 1
            group_images = parse_images(element, camera_id, start_image_id)
            for image_id, image in group_images.items():
                images[image_id] = image
            local_index = 0
            for photo in element.findall("Photo"):
                source_id = int(text(photo, "Id"))
                if not has_complete_pose(photo):
                    continue
                if source_id in image_id_by_source_id:
                    raise ValueError(f"Duplicate Photo Id across photogroups: {source_id}")
                image_id_by_source_id[source_id] = start_image_id + local_index
                local_index += 1
            remove_from_parent(element_stack, element)
        elif element.tag in {"TiePoint", "ControlPoint"}:
            remove_from_parent(element_stack, element)
        elif element.tag == "Photogroups":
            break

        element_stack.pop()
        tag_stack.pop()

    if not cameras:
        raise ValueError("Missing Photogroup element")
    return cameras, images, image_id_by_source_id, references, target_srs_id


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


def write_cameras(path: Path, cameras: dict[int, Camera]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# Camera list with one line of data per camera:\n")
        fh.write("#   CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]\n")
        fh.write(f"# Number of cameras: {len(cameras)}\n")
        for camera_id in sorted(cameras):
            camera = cameras[camera_id]
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


def stream_points_gcps_and_image_points(
    input_path: Path,
    points_tmp: Path,
    temp_image_points: ImagePointTempWriter,
    image_id_by_source_id: dict[int, int],
    references: dict[int, SpatialReference],
    target_srs_id: int,
    max_image_id: int,
) -> tuple[int, int, list]:
    point_count = 0
    observation_count = 0
    image_point_counts = [0] * (max_image_id + 1)
    gcps = []
    element_stack: list[ET.Element] = []

    with points_tmp.open("w", encoding="utf-8", newline="\n") as points_fh:
        for event, element in ET.iterparse(input_path, events=("start", "end")):
            if event == "start":
                element_stack.append(element)
                continue

            if element.tag == "TiePoint":
                position = element.find("Position")
                if position is None:
                    raise ValueError("TiePoint has no Position")
                xyz = tuple(ftext(position, axis) for axis in ("x", "y", "z"))
                rgb = color_to_rgb(element.find("Color"))
                observations = []
                for measurement in element.findall("Measurement"):
                    source_id = int(text(measurement, "PhotoId"))
                    image_id = image_id_by_source_id.get(source_id)
                    if image_id is None:
                        continue
                    x = ftext(measurement, "x")
                    y = ftext(measurement, "y")
                    observations.append((image_id, x, y))
                if len(observations) >= 2:
                    point_count += 1
                    track_items = []
                    for image_id, x, y in observations:
                        point2d_idx = image_point_counts[image_id]
                        image_point_counts[image_id] += 1
                        temp_image_points.write(image_id, x, y, point_count)
                        track_items.append(f"{image_id} {point2d_idx}")
                    xyz_text = " ".join(format_float(value) for value in xyz)
                    r, g, b = rgb
                    track = " ".join(track_items)
                    points_fh.write(f"{point_count} {xyz_text} {r} {g} {b} 0 {track}\n")
                    observation_count += len(observations)
                remove_from_parent(element_stack, element)
            elif element.tag == "ControlPoint":
                gcps.append(
                    control_point_to_gcp(
                        element,
                        len(gcps),
                        image_id_by_source_id,
                        references,
                        target_srs_id,
                        lambda _source_id, x, y: (x, y),
                    )
                )
                remove_from_parent(element_stack, element)
            elif element.tag in {"SRS", "Photogroup"}:
                remove_from_parent(element_stack, element)

            element_stack.pop()

    temp_image_points.close()
    return point_count, observation_count, gcps


def write_images_from_temp(path: Path, images: dict[int, Image], temp_image_points: ImagePointTempWriter) -> None:
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
            temp_path = temp_image_points.path_for_image(image_id)
            if temp_path.exists():
                with temp_path.open(encoding="utf-8") as points_fh:
                    shutil.copyfileobj(points_fh, fh)
            fh.write("\n")


def write_points_from_temp(path: Path, points_tmp: Path, point_count: int) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# 3D point list with one line of data per point:\n")
        fh.write("#   POINT3D_ID, X, Y, Z, R, G, B, ERROR, TRACK[] as (IMAGE_ID, POINT2D_IDX)\n")
        fh.write(f"# Number of points: {point_count}\n")
        with points_tmp.open(encoding="utf-8") as points_fh:
            for line in points_fh:
                fh.write(line)


def main() -> None:
    args = parse_args()
    input_path = args.input.resolve()
    output_dir = args.output.resolve()
    if not input_path.exists():
        raise FileNotFoundError(input_path)

    cameras, images, image_id_by_source_id, references, target_srs_id = stream_metadata(input_path, args.camera_model)

    output_dir.mkdir(parents=True, exist_ok=True)
    temp_root = output_dir / ".points2D.tmp"
    points_tmp = output_dir / ".points3D.tmp"
    if temp_root.exists():
        shutil.rmtree(temp_root)
    temp_image_points = ImagePointTempWriter(temp_root)
    try:
        point_count, measurement_count, gcps = stream_points_gcps_and_image_points(
            input_path,
            points_tmp,
            temp_image_points,
            image_id_by_source_id,
            references,
            target_srs_id,
            max(images) if images else 0,
        )
        write_cameras(output_dir / "cameras.txt", cameras)
        write_images_from_temp(output_dir / "images.txt", images, temp_image_points)
        write_points_from_temp(output_dir / "points3D.txt", points_tmp, point_count)
    finally:
        temp_image_points.close()
        points_tmp.unlink(missing_ok=True)
        if temp_root.exists():
            shutil.rmtree(temp_root)
    observed_gcps = observed_ground_control_points(gcps)
    if observed_gcps:
        write_gcp_files(output_dir / "gcp.txt", output_dir / "gcp_observations.txt", observed_gcps)
    else:
        (output_dir / "gcp.txt").unlink(missing_ok=True)
        (output_dir / "gcp_observations.txt").unlink(missing_ok=True)

    gcp_observations = sum(len(gcp.observations) for gcp in observed_gcps)
    print(f"Wrote COLMAP text model to {output_dir}")
    print(f"  cameras: {len(cameras)}")
    print(f"  images: {len(images)}")
    print(f"  points3D: {point_count}")
    print(f"  observations: {measurement_count}")
    print(f"  gcps: {len(observed_gcps)}")
    print(f"  gcp observations: {gcp_observations}")
    if args.camera_model == "OPENCV":
        print("  note: XML K3 was dropped; use --camera-model FULL_OPENCV to keep it.")


if __name__ == "__main__":
    main()
