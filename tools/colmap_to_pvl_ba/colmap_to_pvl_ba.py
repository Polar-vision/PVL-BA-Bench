#!/usr/bin/env python3
"""Convert COLMAP text model to PVL-BA format."""

from __future__ import annotations

import argparse
import math
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Camera:
    camera_id: int
    model: str
    width: int
    height: int
    params: tuple[float, ...]
    group_id: int


@dataclass(frozen=True)
class Image:
    image_id: int
    row_index: int
    pvl_index: int
    camera_id: int
    rotation: list[list[float]]
    center: tuple[float, float, float]
    points2d: list[tuple[float, float, int]]


@dataclass(frozen=True)
class Point3D:
    point3d_id: int
    xyz: tuple[float, float, float]
    track: list[tuple[int, int]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="COLMAP text model directory")
    parser.add_argument("--output", required=True, type=Path, help="PVL-BA output directory")
    parser.add_argument(
        "--image-index",
        choices=("id_minus_one", "row"),
        default="id_minus_one",
        help="How to map COLMAP IMAGE_ID to PVL-BA image_idx.",
    )
    parser.add_argument(
        "--backend",
        choices=("memory", "sqlite"),
        default="memory",
        help="Use sqlite for large models to avoid keeping all observations in memory.",
    )
    parser.add_argument("--undistort-iterations", type=int, default=20)
    return parser.parse_args()


def read_cameras(path: Path) -> dict[int, Camera]:
    cameras: dict[int, Camera] = {}
    group_by_intrinsics: dict[tuple[str, int, int, tuple[float, ...]], int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        camera_id = int(tokens[0])
        model = tokens[1]
        width = int(tokens[2])
        height = int(tokens[3])
        params = tuple(map(float, tokens[4:]))
        intrinsics_key = (model, width, height, params)
        group_id = group_by_intrinsics.setdefault(intrinsics_key, len(group_by_intrinsics) + 1)
        cameras[camera_id] = Camera(camera_id, model, width, height, params, group_id)
    return cameras


def read_images(path: Path, image_index_mode: str) -> dict[int, Image]:
    images: dict[int, Image] = {}
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
            image_id = int(tokens[0])
            qvec = tuple(map(float, tokens[1:5]))
            tvec = tuple(map(float, tokens[5:8]))
            camera_id = int(tokens[8])
            points_tokens = points_line.split()
            if len(points_tokens) % 3 != 0:
                raise ValueError(f"Malformed POINTS2D for image {image_id}")
            points2d = []
            for index in range(0, len(points_tokens), 3):
                points2d.append(
                    (
                        float(points_tokens[index]),
                        float(points_tokens[index + 1]),
                        int(points_tokens[index + 2]),
                    )
                )
            rotation = qvec_to_rotation(qvec)
            center = camera_center(rotation, tvec)
            pvl_index = image_id - 1 if image_index_mode == "id_minus_one" else row_index
            images[image_id] = Image(image_id, row_index, pvl_index, camera_id, rotation, center, points2d)
            row_index += 1
    return images


def stream_image_headers(path: Path, image_index_mode: str):
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
            image_id = int(tokens[0])
            qvec = tuple(map(float, tokens[1:5]))
            tvec = tuple(map(float, tokens[5:8]))
            camera_id = int(tokens[8])
            rotation = qvec_to_rotation(qvec)
            center = camera_center(rotation, tvec)
            pvl_index = image_id - 1 if image_index_mode == "id_minus_one" else row_index
            yield Image(image_id, row_index, pvl_index, camera_id, rotation, center, [])
            row_index += 1


def read_image_headers(path: Path, image_index_mode: str) -> dict[int, Image]:
    return {image.image_id: image for image in stream_image_headers(path, image_index_mode)}


def read_points3d(path: Path) -> list[Point3D]:
    points: list[Point3D] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        point3d_id = int(tokens[0])
        xyz = tuple(map(float, tokens[1:4]))
        track_tokens = list(map(int, tokens[8:]))
        if len(track_tokens) % 2 != 0:
            raise ValueError(f"Malformed track for point {point3d_id}")
        track = list(zip(track_tokens[0::2], track_tokens[1::2]))
        points.append(Point3D(point3d_id, xyz, track))
    return points


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


def camera_center(rotation: list[list[float]], tvec: tuple[float, float, float]) -> tuple[float, float, float]:
    # COLMAP uses X_cam = R * X_world + t, so C = -R^T * t.
    return tuple(-sum(rotation[j][i] * tvec[j] for j in range(3)) for i in range(3))


def euler_from_rotation(R: list[list[float]]) -> tuple[float, float, float]:
    ex = math.asin(max(-1.0, min(1.0, -R[2][1])))
    c2 = math.cos(ex)
    if abs(c2) > 1e-12:
        ey = math.atan2(-R[2][0], R[2][2])
        ez = math.atan2(R[0][1], R[1][1])
    else:
        ey = 0.0
        ez = math.atan2(-R[1][0], R[0][0])
    return ey, ex, ez


def camera_fx_fy_cx_cy(camera: Camera) -> tuple[float, float, float, float]:
    if camera.model == "PINHOLE":
        fx, fy, cx, cy = camera.params
    elif camera.model == "SIMPLE_PINHOLE":
        f, cx, cy = camera.params
        fx = fy = f
    elif camera.model == "OPENCV":
        fx, fy, cx, cy = camera.params[:4]
    else:
        raise ValueError(f"Unsupported camera model for PVL-BA export: {camera.model}")
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
    if not (math.isfinite(x) and math.isfinite(y)):
        return u, v
    return fx * x + cx, fy * y + cy


def write_cal(path: Path, cameras: dict[int, Camera]) -> None:
    ordered_groups = sorted(cameras.values(), key=lambda camera: camera.group_id)
    seen = set()
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for camera in ordered_groups:
            if camera.group_id in seen:
                continue
            seen.add(camera.group_id)
            fx, fy, cx, cy = camera_fx_fy_cx_cy(camera)
            fh.write(f"{fx:.12f} 0 {cx:.12f}\n")
            fh.write(f"0 {fy:.12f} {cy:.12f}\n")
            fh.write("0 0 1\n")


def write_cam(path: Path, images: dict[int, Image], cameras: dict[int, Camera]) -> None:
    ordered = sorted(images.values(), key=lambda image: image.pvl_index)
    expected = list(range(len(ordered)))
    actual = [image.pvl_index for image in ordered]
    if actual != expected:
        raise ValueError("PVL image indices are not contiguous 0..N-1; use --image-index row if needed")
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for image in ordered:
            ey, ex, ez = euler_from_rotation(image.rotation)
            cx, cy, cz = image.center
            group_id = cameras[image.camera_id].group_id
            fh.write(
                f"{ey:.12f} {ex:.12f} {ez:.12f} "
                f"{cx:.12f} {cy:.12f} {cz:.12f} {group_id}\n"
            )


def write_xyz(path: Path, points: list[Point3D]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for point in points:
            x, y, z = point.xyz
            fh.write(f"{x:.12f} {y:.12f} {z:.12f}\n")


def write_feature(
    path: Path,
    points: list[Point3D],
    images: dict[int, Image],
    cameras: dict[int, Camera],
    iterations: int,
) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for point in points:
            fields = [str(len(point.track))]
            observations = []
            for image_id, point2d_idx in point.track:
                image = images[image_id]
                u, v, observed_point_id = image.points2d[point2d_idx]
                if observed_point_id != point.point3d_id:
                    raise ValueError(
                        f"Track mismatch: point {point.point3d_id}, image {image_id}, "
                        f"point2D {point2d_idx} references {observed_point_id}"
                    )
                uu, vv = undistort_pixel(u, v, cameras[image.camera_id], iterations)
                observations.append((image.pvl_index, uu, vv))
            for image_idx, uu, vv in sorted(observations, key=lambda observation: observation):
                fields.extend((str(image_idx), f"{uu:.12f}", f"{vv:.12f}"))
            fh.write(" ".join(fields))
            fh.write("\n")


def write_feature_sqlite(
    path: Path,
    connection: sqlite3.Connection,
    point_count: int,
) -> int:
    cursor = connection.execute(
        "select point_index, image_index, u, v from observations order by point_index, image_index, u, v"
    )
    observation_count = 0
    current_index = 0
    current_observations: list[tuple[int, float, float]] = []

    def flush_until(target_index: int, fh) -> None:
        nonlocal current_index, current_observations
        while current_index < target_index:
            fields = [str(len(current_observations))]
            for image_index, u, v in current_observations:
                fields.extend((str(image_index), f"{u:.12f}", f"{v:.12f}"))
            fh.write(" ".join(fields))
            fh.write("\n")
            current_index += 1
            current_observations = []

    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for point_index, image_index, u, v in cursor:
            flush_until(point_index, fh)
            current_observations.append((image_index, u, v))
            observation_count += 1
        flush_until(point_count, fh)
    return observation_count


def write_pvl_ba_sqlite(
    input_dir: Path,
    output_dir: Path,
    image_index_mode: str,
    undistort_iterations: int,
) -> tuple[int, int, int, int]:
    cameras = read_cameras(input_dir / "cameras.txt")
    images = read_image_headers(input_dir / "images.txt", image_index_mode)
    ordered = sorted(images.values(), key=lambda image: image.pvl_index)
    expected = list(range(len(ordered)))
    actual = [image.pvl_index for image in ordered]
    if actual != expected:
        raise ValueError("PVL image indices are not contiguous 0..N-1; use --image-index row if needed")

    output_dir.mkdir(parents=True, exist_ok=True)
    write_cal(output_dir / "cal.txt", cameras)
    write_cam(output_dir / f"Cam-{len(images)}-.txt", images, cameras)

    point_id_to_index: dict[int, int] = {}
    with (input_dir / "points3D.txt").open(encoding="utf-8") as src, (output_dir / "XYZ.txt").open(
        "w", encoding="utf-8", newline="\n"
    ) as xyz_fh:
        for line in src:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            tokens = line.split()
            point_id = int(tokens[0])
            point_index = len(point_id_to_index)
            point_id_to_index[point_id] = point_index
            x, y, z = map(float, tokens[1:4])
            xyz_fh.write(f"{x:.12f} {y:.12f} {z:.12f}\n")

    with tempfile.TemporaryDirectory(prefix="colmap_to_pvl_ba_") as tmp:
        db_path = Path(tmp) / "observations.sqlite"
        connection = sqlite3.connect(db_path)
        connection.execute("pragma journal_mode=off")
        connection.execute("pragma synchronous=off")
        connection.execute("pragma temp_store=file")
        connection.execute(
            "create table observations(point_index integer not null, image_index integer not null, u real not null, v real not null)"
        )
        batch = []
        with (input_dir / "images.txt").open(encoding="utf-8") as fh:
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
                image_id = int(tokens[0])
                image = images[image_id]
                camera = cameras[image.camera_id]
                points_tokens = points_line.split()
                if len(points_tokens) % 3 != 0:
                    raise ValueError(f"Malformed POINTS2D for image {image_id}")
                for index in range(0, len(points_tokens), 3):
                    point_id = int(points_tokens[index + 2])
                    if point_id < 0:
                        continue
                    point_index = point_id_to_index.get(point_id)
                    if point_index is None:
                        continue
                    u = float(points_tokens[index])
                    v = float(points_tokens[index + 1])
                    uu, vv = undistort_pixel(u, v, camera, undistort_iterations)
                    batch.append((point_index, image.pvl_index, uu, vv))
                    if len(batch) >= 100_000:
                        connection.executemany("insert into observations values (?, ?, ?, ?)", batch)
                        connection.commit()
                        batch.clear()
        if batch:
            connection.executemany("insert into observations values (?, ?, ?, ?)", batch)
            connection.commit()
        connection.execute("create index observations_point_image on observations(point_index, image_index)")
        connection.commit()
        observation_count = write_feature_sqlite(output_dir / "Feature.txt", connection, len(point_id_to_index))
        connection.close()
    return len({camera.group_id for camera in cameras.values()}), len(images), len(point_id_to_index), observation_count


def main() -> None:
    args = parse_args()
    input_dir = args.input
    if args.backend == "sqlite":
        group_count, image_count, point_count, observations = write_pvl_ba_sqlite(
            input_dir,
            args.output,
            args.image_index,
            args.undistort_iterations,
        )
    else:
        cameras = read_cameras(input_dir / "cameras.txt")
        images = read_images(input_dir / "images.txt", args.image_index)
        points = read_points3d(input_dir / "points3D.txt")

        args.output.mkdir(parents=True, exist_ok=True)
        write_cal(args.output / "cal.txt", cameras)
        write_cam(args.output / f"Cam-{len(images)}-.txt", images, cameras)
        write_xyz(args.output / "XYZ.txt", points)
        write_feature(args.output / "Feature.txt", points, images, cameras, args.undistort_iterations)
        group_count = len({camera.group_id for camera in cameras.values()})
        image_count = len(images)
        point_count = len(points)
        observations = sum(len(point.track) for point in points)
    print(f"Wrote PVL-BA format to {args.output.resolve()}")
    print(f"  camera groups: {group_count}")
    print(f"  cameras/images: {image_count}")
    print(f"  points: {point_count}")
    print(f"  observations: {observations}")


if __name__ == "__main__":
    main()
