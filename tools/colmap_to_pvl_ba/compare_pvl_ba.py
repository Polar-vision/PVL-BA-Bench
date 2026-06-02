#!/usr/bin/env python3
"""Compare two PVL-BA datasets with duplicate-observation set matching."""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--actual", required=True, type=Path)
    parser.add_argument("--expected", required=True, type=Path)
    return parser.parse_args()


def floats(line: str) -> list[float]:
    return [float(item) for item in line.split()]


def max_numeric_file_diff(actual: Path, expected: Path) -> tuple[float, int, int]:
    max_diff = 0.0
    value_count = 0
    line_count = 0
    with actual.open(encoding="utf-8") as actual_fh, expected.open(encoding="utf-8") as expected_fh:
        for line_count, (actual_line, expected_line) in enumerate(zip(actual_fh, expected_fh), start=1):
            actual_values = floats(actual_line)
            expected_values = floats(expected_line)
            if len(actual_values) != len(expected_values):
                raise ValueError(f"Token count mismatch at line {line_count}: {actual} vs {expected}")
            for actual_value, expected_value in zip(actual_values, expected_values):
                max_diff = max(max_diff, abs(actual_value - expected_value))
                value_count += 1
    return max_diff, value_count, line_count


def compare_features(actual: Path, expected: Path) -> tuple[float, int, int, int]:
    max_diff = 0.0
    observation_count = 0
    bad_track_lengths = 0
    bad_image_groups = 0
    with actual.open(encoding="utf-8") as actual_fh, expected.open(encoding="utf-8") as expected_fh:
        for actual_line, expected_line in zip(actual_fh, expected_fh):
            actual_tokens = actual_line.split()
            expected_tokens = expected_line.split()
            actual_track_len = int(actual_tokens[0])
            expected_track_len = int(expected_tokens[0])
            if actual_track_len != expected_track_len:
                bad_track_lengths += 1
                continue

            actual_groups = grouped_observations(actual_tokens, actual_track_len)
            expected_groups = grouped_observations(expected_tokens, expected_track_len)
            if set(actual_groups) != set(expected_groups):
                bad_image_groups += 1
                continue

            for image_idx, actual_observations in actual_groups.items():
                expected_observations = expected_groups[image_idx]
                if len(actual_observations) != len(expected_observations):
                    bad_image_groups += 1
                    continue
                used = [False] * len(expected_observations)
                for u, v in actual_observations:
                    best_diff = None
                    best_index = None
                    for index, (expected_u, expected_v) in enumerate(expected_observations):
                        if used[index]:
                            continue
                        diff = max(abs(u - expected_u), abs(v - expected_v))
                        if best_diff is None or diff < best_diff:
                            best_diff = diff
                            best_index = index
                    if best_index is None:
                        bad_image_groups += 1
                        continue
                    used[best_index] = True
                    max_diff = max(max_diff, best_diff or 0.0)
                    observation_count += 1
    return max_diff, observation_count, bad_track_lengths, bad_image_groups


def grouped_observations(tokens: list[str], track_len: int) -> dict[int, list[tuple[float, float]]]:
    groups: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for index in range(track_len):
        image_idx = int(tokens[1 + 3 * index])
        u = float(tokens[2 + 3 * index])
        v = float(tokens[3 + 3 * index])
        groups[image_idx].append((u, v))
    return groups


def main() -> None:
    args = parse_args()
    for name in ("cal.txt", "XYZ.txt"):
        diff, values, lines = max_numeric_file_diff(args.actual / name, args.expected / name)
        print(f"{name}: max_diff={diff} values={values} lines={lines}")

    actual_cam = sorted(args.actual.glob("Cam*.txt"))[0]
    expected_cam = sorted(args.expected.glob("Cam*.txt"))[0]
    diff, values, lines = max_numeric_file_diff(actual_cam, expected_cam)
    print(f"Cam: max_diff={diff} values={values} lines={lines}")

    diff, observations, bad_lengths, bad_groups = compare_features(args.actual / "Feature.txt", args.expected / "Feature.txt")
    print(
        "Feature: "
        f"set_matched_max_uv_diff={diff} observations={observations} "
        f"bad_track_lengths={bad_lengths} bad_image_groups={bad_groups}"
    )


if __name__ == "__main__":
    main()

