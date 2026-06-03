#!/usr/bin/env python3
"""Verify GCP sidecar reprojection error for a COLMAP text model."""

from __future__ import annotations

import argparse
import math
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path)
    return parser.parse_args()


def qvec_to_rotation(qvec: tuple[float, float, float, float]) -> list[list[float]]:
    qw, qx, qy, qz = qvec
    return [
        [1.0 - 2.0 * qy * qy - 2.0 * qz * qz, 2.0 * qx * qy - 2.0 * qz * qw, 2.0 * qx * qz + 2.0 * qy * qw],
        [2.0 * qx * qy + 2.0 * qz * qw, 1.0 - 2.0 * qx * qx - 2.0 * qz * qz, 2.0 * qy * qz - 2.0 * qx * qw],
        [2.0 * qx * qz - 2.0 * qy * qw, 2.0 * qy * qz + 2.0 * qx * qw, 1.0 - 2.0 * qx * qx - 2.0 * qy * qy],
    ]


def mat_vec(matrix: list[list[float]], vector: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(sum(matrix[i][j] * vector[j] for j in range(3)) for i in range(3))


def read_camera(path: Path) -> tuple[str, tuple[float, ...]]:
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        tokens = line.split()
        return tokens[1], tuple(map(float, tokens[4:]))
    raise ValueError("No camera found")


def read_images(path: Path) -> dict[int, tuple[list[list[float]], tuple[float, float, float]]]:
    images = {}
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    for index in range(0, len(lines), 2):
        tokens = lines[index].split()
        image_id = int(tokens[0])
        qvec = tuple(map(float, tokens[1:5]))
        tvec = tuple(map(float, tokens[5:8]))
        images[image_id] = (qvec_to_rotation(qvec), tvec)
    return images


def project_colmap(point_camera: tuple[float, float, float], model: str, params: tuple[float, ...]) -> tuple[float, float]:
    x = point_camera[0] / point_camera[2]
    y = point_camera[1] / point_camera[2]
    if model == "OPENCV":
        fx, fy, cx, cy, k1, k2, p1, p2 = params
        r2 = x * x + y * y
        radial = 1.0 + k1 * r2 + k2 * r2 * r2
        xd = x * radial + 2.0 * p1 * x * y + p2 * (r2 + 2.0 * x * x)
        yd = y * radial + p1 * (r2 + 2.0 * y * y) + 2.0 * p2 * x * y
        return fx * xd + cx, fy * yd + cy
    if model == "FULL_OPENCV":
        fx, fy, cx, cy, k1, k2, k3, k4, k5, k6, p1, p2 = params
        r2 = x * x + y * y
        r4 = r2 * r2
        r6 = r4 * r2
        radial = (1.0 + k1 * r2 + k2 * r4 + k3 * r6) / (1.0 + k4 * r2 + k5 * r4 + k6 * r6)
        xd = x * radial + 2.0 * p1 * x * y + p2 * (r2 + 2.0 * x * x)
        yd = y * radial + p1 * (r2 + 2.0 * y * y) + 2.0 * p2 * x * y
        return fx * xd + cx, fy * yd + cy
    raise ValueError(f"Unsupported model: {model}")


def print_stats(errors: list[float]) -> None:
    errors.sort()
    n = len(errors)
    sum_squared = sum(error * error for error in errors)
    print(f"observations {n}")
    print(f"mean {sum(errors) / n}")
    print(f"rmse {math.sqrt(sum_squared / n)}")
    print(f"median {errors[n // 2]}")
    print(f"p95 {errors[int(0.95 * n)]}")
    print(f"max {errors[-1]}")


def main() -> None:
    args = parse_args()
    model, params = read_camera(args.input_dir / "cameras.txt")
    images = read_images(args.input_dir / "images.txt")
    gcps = []
    for line in (args.input_dir / "gcp.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        tokens = line.split()
        gcps.append(tuple(map(float, tokens[2:5])))
    errors = []
    for line in (args.input_dir / "gcp_observations.txt").read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        tokens = line.split()
        gcp_id = int(tokens[0])
        track_len = int(tokens[1])
        point = gcps[gcp_id]
        for observation_index in range(track_len):
            image_id = int(tokens[2 + 3 * observation_index])
            observed_x = float(tokens[3 + 3 * observation_index])
            observed_y = float(tokens[4 + 3 * observation_index])
            rotation, translation = images[image_id]
            pc = mat_vec(rotation, point)
            pc = tuple(pc[index] + translation[index] for index in range(3))
            projected_x, projected_y = project_colmap(pc, model, params)
            errors.append(math.hypot(projected_x - observed_x, projected_y - observed_y))
    print_stats(errors)


if __name__ == "__main__":
    main()
