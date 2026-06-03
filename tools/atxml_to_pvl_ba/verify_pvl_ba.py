#!/usr/bin/env python3
"""Verify reprojection error for PVL-BA format."""

from __future__ import annotations

import argparse
import math
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--samples", type=int, default=0, help="0 means all observations")
    return parser.parse_args()


def rotation_from_euler(ey: float, ex: float, ez: float) -> list[list[float]]:
    c1 = math.cos(ey)
    c2 = math.cos(ex)
    c3 = math.cos(ez)
    s1 = math.sin(ey)
    s2 = math.sin(ex)
    s3 = math.sin(ez)
    return [
        [c1 * c3 - s1 * s2 * s3, c2 * s3, s1 * c3 + c1 * s2 * s3],
        [-c1 * s3 - s1 * s2 * c3, c2 * c3, -s1 * s3 + c1 * s2 * c3],
        [-s1 * c2, -s2, c1 * c2],
    ]


def mat_vec(matrix: list[list[float]], vector: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        sum(matrix[0][j] * vector[j] for j in range(3)),
        sum(matrix[1][j] * vector[j] for j in range(3)),
        sum(matrix[2][j] * vector[j] for j in range(3)),
    )


def print_error_stats(label: str, errors: list[float], sum_squared: float) -> None:
    errors.sort()
    n = len(errors)
    print(label)
    print(f"observations {n}")
    print(f"mean {sum(errors) / n}")
    print(f"rmse {math.sqrt(sum_squared / n)}")
    print(f"median {errors[n // 2]}")
    print(f"p90 {errors[int(0.90 * n)]}")
    print(f"p95 {errors[int(0.95 * n)]}")
    print(f"p99 {errors[int(0.99 * n)]}")
    print(f"max {errors[-1]}")


def read_intrinsics(path: Path) -> list[tuple[float, float, float, float]]:
    rows = [list(map(float, line.split())) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    intrinsics = []
    for index in range(0, len(rows), 3):
        K = rows[index : index + 3]
        intrinsics.append((K[0][0], K[1][1], K[0][2], K[1][2]))
    return intrinsics


def find_cam_file(input_dir: Path) -> Path:
    matches = sorted(input_dir.glob("Cam*-*.txt"))
    if not matches:
        matches = sorted(input_dir.glob("Cam.txt"))
    if not matches:
        raise FileNotFoundError("No Cam file found")
    return matches[0]


def main() -> None:
    args = parse_args()
    input_dir = args.input_dir
    intrinsics = read_intrinsics(input_dir / "cal.txt")
    cameras = []
    for line in find_cam_file(input_dir).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        values = list(map(float, line.split()))
        ey, ex, ez = values[:3]
        center = tuple(values[3:6])
        group = int(values[6]) - 1
        cameras.append((rotation_from_euler(ey, ex, ez), center, intrinsics[group]))

    errors: list[float] = []
    sum_squared = 0.0
    min_depth = float("inf")
    max_depth = -float("inf")
    negative_depth = 0
    observation_count = 0

    with (input_dir / "XYZ.txt").open(encoding="utf-8") as xyz_file, (input_dir / "Feature.txt").open(
        encoding="utf-8"
    ) as feature_file:
        for xyz_line, feature_line in zip(xyz_file, feature_file):
            point = tuple(map(float, xyz_line.split()))
            tokens = feature_line.split()
            track_len = int(tokens[0])
            for observation_index in range(track_len):
                image_index = int(tokens[1 + 3 * observation_index])
                observed_u = float(tokens[2 + 3 * observation_index])
                observed_v = float(tokens[3 + 3 * observation_index])
                rotation, center, K = cameras[image_index]
                vector = tuple(point[i] - center[i] for i in range(3))
                x_cam, y_cam, z_cam = mat_vec(rotation, vector)
                min_depth = min(min_depth, z_cam)
                max_depth = max(max_depth, z_cam)
                if z_cam < 0.0:
                    negative_depth += 1
                fx, fy, cx, cy = K
                projected_u = fx * x_cam / z_cam + cx
                projected_v = fy * y_cam / z_cam + cy
                error = math.hypot(projected_u - observed_u, projected_v - observed_v)
                errors.append(error)
                sum_squared += error * error
                observation_count += 1
                if args.samples and observation_count >= args.samples:
                    break
            if args.samples and observation_count >= args.samples:
                break

    print_error_stats("tie points", errors, sum_squared)
    print(f"depth_min {min_depth} depth_max {max_depth} negative_depth_count {negative_depth}")

    gcp_path = input_dir / "gcp.txt"
    gcp_obs_path = input_dir / "gcp_observations.txt"
    if gcp_path.exists() and gcp_obs_path.exists():
        gcps = []
        for line in gcp_path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            tokens = line.split()
            gcps.append(tuple(map(float, tokens[2:5])))
        gcp_errors = []
        gcp_sum_squared = 0.0
        for line in gcp_obs_path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            tokens = line.split()
            gcp_id = int(tokens[0])
            track_len = int(tokens[1])
            point = gcps[gcp_id]
            for observation_index in range(track_len):
                image_index = int(tokens[2 + 3 * observation_index])
                observed_u = float(tokens[3 + 3 * observation_index])
                observed_v = float(tokens[4 + 3 * observation_index])
                rotation, center, K = cameras[image_index]
                vector = tuple(point[i] - center[i] for i in range(3))
                x_cam, y_cam, z_cam = mat_vec(rotation, vector)
                fx, fy, cx, cy = K
                projected_u = fx * x_cam / z_cam + cx
                projected_v = fy * y_cam / z_cam + cy
                error = math.hypot(projected_u - observed_u, projected_v - observed_v)
                gcp_errors.append(error)
                gcp_sum_squared += error * error
        if gcp_errors:
            print_error_stats("gcps", gcp_errors, gcp_sum_squared)


if __name__ == "__main__":
    main()
