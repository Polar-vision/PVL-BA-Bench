#!/usr/bin/env python3
"""Convert a COLMAP text model to a normalized BAL file."""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path


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
    row_index: int
    qvec: tuple[float, float, float, float]
    tvec: tuple[float, float, float]
    camera_id: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="COLMAP text model directory")
    parser.add_argument("--output", required=True, type=Path, help="Output BAL file")
    parser.add_argument("--undistort-iterations", type=int, default=20)
    return parser.parse_args()


def read_cameras(path: Path) -> dict[int, Camera]:
    cameras: dict[int, Camera] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        camera_id = int(tokens[0])
        cameras[camera_id] = Camera(
            camera_id=camera_id,
            model=tokens[1],
            width=int(tokens[2]),
            height=int(tokens[3]),
            params=tuple(map(float, tokens[4:])),
        )
    return cameras


def stream_images(path: Path):
    row_index = 0
    with path.open(encoding="utf-8") as fh:
        while True:
            header = fh.readline()
            if not header:
                break
            header = header.strip()
            if not header or header.startswith("#"):
                continue
            points_line = fh.readline()
            if points_line == "":
                raise ValueError("Malformed images.txt: missing POINTS2D line")
            tokens = header.split()
            points_tokens = points_line.split()
            if len(points_tokens) % 3 != 0:
                raise ValueError(f"Malformed POINTS2D for image {tokens[0]}")
            yield (
                Image(
                    image_id=int(tokens[0]),
                    row_index=row_index,
                    qvec=tuple(map(float, tokens[1:5])),
                    tvec=tuple(map(float, tokens[5:8])),
                    camera_id=int(tokens[8]),
                ),
                points_tokens,
            )
            row_index += 1


def read_image_headers(path: Path) -> dict[int, Image]:
    return {image.image_id: image for image, _points in stream_images(path)}


def read_points_to_tmp(path: Path, points_tmp: Path) -> dict[int, int]:
    point_id_to_index: dict[int, int] = {}
    with path.open(encoding="utf-8") as src, points_tmp.open("w", encoding="utf-8", newline="\n") as dst:
        for line in src:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            tokens = line.split()
            point_id = int(tokens[0])
            point_index = len(point_id_to_index)
            point_id_to_index[point_id] = point_index
            x, y, z = map(float, tokens[1:4])
            dst.write(f"{x:.17g}\n{y:.17g}\n{z:.17g}\n")
    return point_id_to_index


def camera_fx_fy_cx_cy(camera: Camera) -> tuple[float, float, float, float]:
    if camera.model == "PINHOLE":
        fx, fy, cx, cy = camera.params
    elif camera.model == "SIMPLE_PINHOLE":
        f, cx, cy = camera.params
        fx = fy = f
    elif camera.model == "OPENCV":
        fx, fy, cx, cy = camera.params[:4]
    else:
        raise ValueError(f"Unsupported camera model for BAL export: {camera.model}")
    return fx, fy, cx, cy


def undistort_pixel(u: float, v: float, camera: Camera, iterations: int) -> tuple[float, float]:
    if camera.model in ("PINHOLE", "SIMPLE_PINHOLE"):
        return u, v
    if camera.model != "OPENCV":
        raise ValueError(f"Unsupported distortion model: {camera.model}")
    fx, fy, cx, cy, k1, k2, p1, p2 = camera.params[:8]
    xd = (u - cx) / fx
    yd = (v - cy) / fy
    x = xd
    y = yd
    for _ in range(iterations):
        r2 = x * x + y * y
        radial = 1.0 + k1 * r2 + k2 * r2 * r2
        px = x * radial + 2.0 * p1 * x * y + p2 * (r2 + 2.0 * x * x)
        py = y * radial + p1 * (r2 + 2.0 * y * y) + 2.0 * p2 * x * y
        x += xd - px
        y += yd - py
        if not (math.isfinite(x) and math.isfinite(y)):
            return u, v
    return fx * x + cx, fy * y + cy


def qvec_to_rotation(qvec: tuple[float, float, float, float]) -> list[list[float]]:
    qw, qx, qy, qz = qvec
    return [
        [
            1.0 - 2.0 * qy * qy - 2.0 * qz * qz,
            2.0 * qx * qy - 2.0 * qw * qz,
            2.0 * qz * qx + 2.0 * qw * qy,
        ],
        [
            2.0 * qx * qy + 2.0 * qw * qz,
            1.0 - 2.0 * qx * qx - 2.0 * qz * qz,
            2.0 * qy * qz - 2.0 * qw * qx,
        ],
        [
            2.0 * qz * qx - 2.0 * qw * qy,
            2.0 * qy * qz + 2.0 * qw * qx,
            1.0 - 2.0 * qx * qx - 2.0 * qy * qy,
        ],
    ]


def rotation_to_angle_axis(rotation: list[list[float]]) -> tuple[float, float, float]:
    trace = rotation[0][0] + rotation[1][1] + rotation[2][2]
    cos_theta = max(-1.0, min(1.0, (trace - 1.0) * 0.5))
    theta = math.acos(cos_theta)
    if theta < 1e-14:
        return (0.0, 0.0, 0.0)
    if abs(math.pi - theta) < 1e-6:
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


def write_observations_tmp(
    images_path: Path,
    observations_tmp: Path,
    cameras: dict[int, Camera],
    point_id_to_index: dict[int, int],
    iterations: int,
) -> int:
    observation_count = 0
    with observations_tmp.open("w", encoding="utf-8", newline="\n") as dst:
        for image, points_tokens in stream_images(images_path):
            camera = cameras[image.camera_id]
            fx, fy, cx, cy = camera_fx_fy_cx_cy(camera)
            for index in range(0, len(points_tokens), 3):
                point_id = int(points_tokens[index + 2])
                point_index = point_id_to_index.get(point_id)
                if point_index is None:
                    continue
                u = float(points_tokens[index])
                v = float(points_tokens[index + 1])
                uu, vv = undistort_pixel(u, v, camera, iterations)
                x = (uu - cx) / fx
                y = (vv - cy) / fy
                dst.write(f"{image.row_index} {point_index} {x:.17g} {y:.17g}\n")
                observation_count += 1
    return observation_count


def write_bal(
    output_path: Path,
    cameras: dict[int, Camera],
    images: dict[int, Image],
    point_count: int,
    observation_count: int,
    observations_tmp: Path,
    points_tmp: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ordered_images = sorted(images.values(), key=lambda image: image.row_index)
    with output_path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"{len(ordered_images)} {point_count} {observation_count}\n")
        with observations_tmp.open(encoding="utf-8") as src:
            for line in src:
                fh.write(line)
        for image in ordered_images:
            angle_axis = rotation_to_angle_axis(qvec_to_rotation(image.qvec))
            for value in (*angle_axis, *image.tvec, 1.0, 0.0, 0.0):
                fh.write(f"{value:.17g}\n")
        with points_tmp.open(encoding="utf-8") as src:
            for line in src:
                fh.write(line)


def main() -> None:
    args = parse_args()
    input_dir = args.input
    output_path = args.output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cameras = read_cameras(input_dir / "cameras.txt")
    images = read_image_headers(input_dir / "images.txt")
    observations_tmp = output_path.with_suffix(output_path.suffix + ".observations.tmp")
    points_tmp = output_path.with_suffix(output_path.suffix + ".points.tmp")
    try:
        point_id_to_index = read_points_to_tmp(input_dir / "points3D.txt", points_tmp)
        observation_count = write_observations_tmp(
            input_dir / "images.txt",
            observations_tmp,
            cameras,
            point_id_to_index,
            args.undistort_iterations,
        )
        write_bal(output_path, cameras, images, len(point_id_to_index), observation_count, observations_tmp, points_tmp)
    finally:
        observations_tmp.unlink(missing_ok=True)
        points_tmp.unlink(missing_ok=True)
    print(f"Wrote BAL file to {output_path.resolve()}")
    print("  mode: normalized")
    print(f"  cameras: {len(images)}")
    print(f"  points: {len(point_id_to_index)}")
    print(f"  observations: {observation_count}")


if __name__ == "__main__":
    main()
