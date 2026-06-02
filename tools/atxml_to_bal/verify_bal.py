#!/usr/bin/env python3
"""Verify reprojection error for a BAL file."""

from __future__ import annotations

import argparse
import math
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--samples", type=int, default=0, help="0 means all observations")
    return parser.parse_args()


def angle_axis_to_rotation(angle_axis: tuple[float, float, float]) -> list[list[float]]:
    theta = math.sqrt(sum(value * value for value in angle_axis))
    if theta < 1e-14:
        return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    wx, wy, wz = (value / theta for value in angle_axis)
    c = math.cos(theta)
    s = math.sin(theta)
    one_c = 1.0 - c
    return [
        [c + wx * wx * one_c, wx * wy * one_c - wz * s, wx * wz * one_c + wy * s],
        [wy * wx * one_c + wz * s, c + wy * wy * one_c, wy * wz * one_c - wx * s],
        [wz * wx * one_c - wy * s, wz * wy * one_c + wx * s, c + wz * wz * one_c],
    ]


def mat_vec(matrix: list[list[float]], vector: tuple[float, float, float]) -> tuple[float, float, float]:
    return (
        sum(matrix[0][j] * vector[j] for j in range(3)),
        sum(matrix[1][j] * vector[j] for j in range(3)),
        sum(matrix[2][j] * vector[j] for j in range(3)),
    )


def main() -> None:
    args = parse_args()
    tokens = args.input.read_text(encoding="utf-8").split()
    cursor = 0
    num_cameras = int(tokens[cursor])
    num_points = int(tokens[cursor + 1])
    num_observations = int(tokens[cursor + 2])
    cursor += 3
    observations = []
    for _ in range(num_observations):
        camera_index = int(tokens[cursor])
        point_index = int(tokens[cursor + 1])
        x = float(tokens[cursor + 2])
        y = float(tokens[cursor + 3])
        observations.append((camera_index, point_index, x, y))
        cursor += 4
    cameras = []
    for _ in range(num_cameras):
        params = tuple(float(tokens[cursor + index]) for index in range(9))
        cameras.append(params)
        cursor += 9
    points = []
    for _ in range(num_points):
        points.append(tuple(float(tokens[cursor + index]) for index in range(3)))
        cursor += 3

    errors = []
    sum_squared = 0.0
    min_depth = float("inf")
    max_depth = -float("inf")
    negative_depth = 0
    for observation_index, (camera_index, point_index, observed_x, observed_y) in enumerate(observations):
        camera = cameras[camera_index]
        rotation = angle_axis_to_rotation(camera[:3])
        translation = camera[3:6]
        focal = camera[6]
        point = points[point_index]
        pc = mat_vec(rotation, point)
        pc = tuple(pc[index] + translation[index] for index in range(3))
        min_depth = min(min_depth, pc[2])
        max_depth = max(max_depth, pc[2])
        if pc[2] < 0.0:
            negative_depth += 1
        projected_x = focal * pc[0] / pc[2]
        projected_y = focal * pc[1] / pc[2]
        error = math.hypot(projected_x - observed_x, projected_y - observed_y)
        errors.append(error)
        sum_squared += error * error
        if args.samples and observation_index + 1 >= args.samples:
            break

    errors.sort()
    n = len(errors)
    print(f"observations {n}")
    print(f"mean {sum(errors) / n}")
    print(f"rmse {math.sqrt(sum_squared / n)}")
    print(f"median {errors[n // 2]}")
    print(f"p90 {errors[int(0.90 * n)]}")
    print(f"p95 {errors[int(0.95 * n)]}")
    print(f"p99 {errors[int(0.99 * n)]}")
    print(f"max {errors[-1]}")
    print(f"depth_min {min_depth} depth_max {max_depth} negative_depth_count {negative_depth}")


if __name__ == "__main__":
    main()

