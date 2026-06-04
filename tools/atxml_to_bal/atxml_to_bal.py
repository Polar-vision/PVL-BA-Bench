#!/usr/bin/env python3
"""Convert BlocksExchange AT.xml to BAL format."""

from __future__ import annotations

import argparse
import math
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from pvl_ba_utils.blocksexchange import (  # noqa: E402
    SpatialReference,
    control_point_to_gcp,
    has_complete_pose,
    remove_from_parent,
    write_gcp_files,
)

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
    intrinsics: Intrinsics


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


def focal_length_pixels(photogroup: ET.Element) -> float:
    value = photogroup.findtext("FocalLengthPixels")
    if value is not None:
        return float(value)
    dimensions = photogroup.find("ImageDimensions")
    if dimensions is None:
        raise ValueError("Missing ImageDimensions")
    sensor_size = ftext(photogroup, "SensorSize")
    if sensor_size == 0.0:
        raise ValueError("SensorSize must be nonzero when FocalLengthPixels is missing")
    max_dimension = max(int(text(dimensions, "Width")), int(text(dimensions, "Height")))
    return ftext(photogroup, "FocalLength") / sensor_size * max_dimension


def parse_intrinsics(photogroup: ET.Element) -> Intrinsics:
    principal = photogroup.find("PrincipalPoint")
    distortion = photogroup.find("Distortion")
    if principal is None or distortion is None:
        raise ValueError("Missing PrincipalPoint or Distortion")
    return Intrinsics(
        focal=focal_length_pixels(photogroup),
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


def parse_cameras(photogroup: ET.Element, intrinsics: Intrinsics, start_index: int) -> dict[int, Camera]:
    cameras: dict[int, Camera] = {}
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
        cameras[source_id] = Camera(source_id, start_index + len(cameras), parse_rotation(rotation_element), center, intrinsics)
    return cameras


def parse_photogroups(block: ET.Element) -> dict[int, Camera]:
    photogroups = block.findall("Photogroups/Photogroup")
    if not photogroups:
        raise ValueError("Missing Photogroup")
    cameras: dict[int, Camera] = {}
    for photogroup in photogroups:
        intrinsics = parse_intrinsics(photogroup)
        group_cameras = parse_cameras(photogroup, intrinsics, len(cameras))
        for source_id, camera in group_cameras.items():
            if source_id in cameras:
                raise ValueError(f"Duplicate Photo Id across photogroups: {source_id}")
            cameras[source_id] = camera
    return cameras


def stream_metadata(input_path: Path) -> tuple[dict[int, Camera], dict[int, SpatialReference], int]:
    cameras: dict[int, Camera] = {}
    references: dict[int, SpatialReference] = {}
    target_srs_id: int | None = None
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
            intrinsics = parse_intrinsics(element)
            group_cameras = parse_cameras(element, intrinsics, len(cameras))
            for source_id, camera in group_cameras.items():
                if source_id in cameras:
                    raise ValueError(f"Duplicate Photo Id across photogroups: {source_id}")
                cameras[source_id] = camera
            remove_from_parent(element_stack, element)
        elif element.tag in {"TiePoint", "ControlPoint"}:
            remove_from_parent(element_stack, element)
        elif element.tag == "Photogroups" and target_srs_id is not None:
            break

        element_stack.pop()
        tag_stack.pop()

    if not cameras:
        raise ValueError("Missing valid cameras")
    if target_srs_id is None:
        raise ValueError("Missing Block/SRSId")
    return cameras, references, target_srs_id


def parse_points(block: ET.Element, cameras: dict[int, Camera], mode: str, iterations: int) -> list[Point]:
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
            if source_id not in cameras:
                continue
            intrinsics = cameras[source_id].intrinsics
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
        if len(observations) >= 2:
            observations.sort(key=lambda observation: observation)
            points.append(Point(xyz, observations))
    return points


def stream_points_and_gcps(
    input_path: Path,
    observations_tmp: Path,
    points_tmp: Path,
    cameras: dict[int, Camera],
    mode: str,
    iterations: int,
    references: dict[int, SpatialReference],
    target_srs_id: int,
) -> tuple[int, int, list]:
    point_count = 0
    observation_count = 0
    gcps = []
    camera_index_by_source_id = {source_id: camera.index for source_id, camera in cameras.items()}
    element_stack: list[ET.Element] = []

    with observations_tmp.open("w", encoding="utf-8", newline="\n") as obs_fh, points_tmp.open(
        "w", encoding="utf-8", newline="\n"
    ) as points_fh:
        for event, element in ET.iterparse(input_path, events=("start", "end")):
            if event == "start":
                element_stack.append(element)
                continue

            if element.tag == "TiePoint":
                position = element.find("Position")
                if position is None:
                    raise ValueError("TiePoint without Position")
                xyz = tuple(ftext(position, axis) for axis in ("x", "y", "z"))
                observations = []
                for measurement in element.findall("Measurement"):
                    source_id = int(text(measurement, "PhotoId"))
                    if source_id not in cameras:
                        continue
                    intrinsics = cameras[source_id].intrinsics
                    x, y = transform_observation_for_mode(
                        ftext(measurement, "x"),
                        ftext(measurement, "y"),
                        intrinsics,
                        mode,
                        iterations,
                    )
                    observations.append((cameras[source_id].index, x, y))
                if len(observations) >= 2:
                    observations.sort(key=lambda observation: observation)
                    for camera_index, x, y in observations:
                        obs_fh.write(f"{camera_index} {point_count} {x:.17g} {y:.17g}\n")
                    for value in xyz:
                        points_fh.write(f"{value:.17g}\n")
                    point_count += 1
                    observation_count += len(observations)
                remove_from_parent(element_stack, element)
            elif element.tag == "ControlPoint":
                gcps.append(
                    control_point_to_gcp(
                        element,
                        len(gcps),
                        camera_index_by_source_id,
                        references,
                        target_srs_id,
                        lambda source_id, x, y: transform_observation_for_mode(
                            x, y, cameras[source_id].intrinsics, mode, iterations
                        ),
                    )
                )
                remove_from_parent(element_stack, element)
            elif element.tag in {"SRS", "Photogroup"}:
                remove_from_parent(element_stack, element)

            element_stack.pop()

    return point_count, observation_count, gcps


def transform_observation_for_mode(
    u_distorted: float,
    v_distorted: float,
    intrinsics: Intrinsics,
    mode: str,
    iterations: int,
) -> tuple[float, float]:
    u, v = undistort_pixel(u_distorted, v_distorted, intrinsics, iterations)
    if mode == "normalized":
        return (u - intrinsics.cx) / intrinsics.focal, (v - intrinsics.cy) / intrinsics.focal
    return u - intrinsics.cx, v - intrinsics.cy


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


def write_bal(path: Path, cameras: dict[int, Camera], points: list[Point], mode: str) -> None:
    ordered_cameras = sorted(cameras.values(), key=lambda camera: camera.index)
    observations = [(camera_index, point_index, x, y) for point_index, point in enumerate(points) for camera_index, x, y in point.observations]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"{len(ordered_cameras)} {len(points)} {len(observations)}\n")
        for camera_index, point_index, x, y in observations:
            fh.write(f"{camera_index} {point_index} {x:.17g} {y:.17g}\n")
        for camera in ordered_cameras:
            angle_axis = rotation_to_angle_axis(camera.rotation)
            translation = translation_from_center(camera.rotation, camera.center)
            focal = 1.0 if mode == "normalized" else camera.intrinsics.focal
            for value in (*angle_axis, *translation, focal, 0.0, 0.0):
                fh.write(f"{value:.17g}\n")
        for point in points:
            for value in point.xyz:
                fh.write(f"{value:.17g}\n")


def write_bal_from_streams(
    path: Path,
    cameras: dict[int, Camera],
    observation_count: int,
    point_count: int,
    observations_tmp: Path,
    points_tmp: Path,
    mode: str,
) -> None:
    ordered_cameras = sorted(cameras.values(), key=lambda camera: camera.index)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"{len(ordered_cameras)} {point_count} {observation_count}\n")
        with observations_tmp.open(encoding="utf-8") as obs_fh:
            for line in obs_fh:
                fh.write(line)
        for camera in ordered_cameras:
            angle_axis = rotation_to_angle_axis(camera.rotation)
            translation = translation_from_center(camera.rotation, camera.center)
            focal = 1.0 if mode == "normalized" else camera.intrinsics.focal
            for value in (*angle_axis, *translation, focal, 0.0, 0.0):
                fh.write(f"{value:.17g}\n")
        with points_tmp.open(encoding="utf-8") as points_fh:
            for line in points_fh:
                fh.write(line)


def main() -> None:
    args = parse_args()
    cameras, references, target_srs_id = stream_metadata(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    observations_tmp = args.output.with_suffix(args.output.suffix + ".observations.tmp")
    points_tmp = args.output.with_suffix(args.output.suffix + ".points.tmp")
    try:
        point_count, observation_count, gcps = stream_points_and_gcps(
            args.input,
            observations_tmp,
            points_tmp,
            cameras,
            args.mode,
            args.undistort_iterations,
            references,
            target_srs_id,
        )
        write_bal_from_streams(args.output, cameras, observation_count, point_count, observations_tmp, points_tmp, args.mode)
    finally:
        observations_tmp.unlink(missing_ok=True)
        points_tmp.unlink(missing_ok=True)
    if gcps:
        write_gcp_files(args.output.with_suffix(".gcp.txt"), args.output.with_suffix(".gcp_observations.txt"), gcps)
    gcp_observations = sum(len(gcp.observations) for gcp in gcps)
    print(f"Wrote BAL file to {args.output.resolve()}")
    print(f"  mode: {args.mode}")
    print(f"  cameras: {len(cameras)}")
    print(f"  points: {point_count}")
    print(f"  observations: {observation_count}")
    print(f"  gcps: {len(gcps)}")
    print(f"  gcp observations: {gcp_observations}")


if __name__ == "__main__":
    main()
