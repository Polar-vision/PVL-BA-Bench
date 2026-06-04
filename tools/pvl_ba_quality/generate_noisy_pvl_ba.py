#!/usr/bin/env python3
"""Generate target-RMSE noisy PVL-BA datasets."""

from __future__ import annotations

import argparse
import json
import math
import random
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Camera:
    rotation: list[list[float]]
    center: tuple[float, float, float]
    intrinsics: tuple[float, float, float, float]


@dataclass(frozen=True)
class ObservationRef:
    point_index: int
    observation_index: int
    residual: tuple[float, float]
    noise: tuple[float, float]


MAIN_RMSE_PRESET = (2.0, 5.0, 10.0, 20.0, 50.0, 100.0)
STRESS_RMSE_PRESET = (200.0, 500.0)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path, help="Input PVL-BA dataset directory")
    parser.add_argument("--output-root", required=True, type=Path, help="Directory where noisy datasets are created")
    parser.add_argument("--target-rmse", type=float, nargs="+", help="Target initial RMSE values in pixels")
    parser.add_argument(
        "--preset",
        choices=("main", "stress", "all"),
        default="main",
        help="Target-RMSE preset used when --target-rmse is omitted.",
    )
    parser.add_argument("--seed", type=int, default=20260603)
    parser.add_argument("--prefix", default="problem")
    parser.add_argument("--include-gcp-observations", action="store_true", help="Also perturb gcp_observations.txt")
    return parser.parse_args()


def target_rmse_values(args: argparse.Namespace) -> list[float]:
    if args.target_rmse:
        return args.target_rmse
    if args.preset == "main":
        return list(MAIN_RMSE_PRESET)
    if args.preset == "stress":
        return list(STRESS_RMSE_PRESET)
    return list(MAIN_RMSE_PRESET + STRESS_RMSE_PRESET)


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
    return tuple(sum(matrix[row][col] * vector[col] for col in range(3)) for row in range(3))


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
        raise FileNotFoundError("No PVL-BA camera file found")
    return matches[0]


def read_cameras(input_dir: Path) -> list[Camera]:
    intrinsics = read_intrinsics(input_dir / "cal.txt")
    cameras = []
    for line in find_cam_file(input_dir).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        values = list(map(float, line.split()))
        group_index = int(values[6]) - 1
        cameras.append(
            Camera(
                rotation=rotation_from_euler(values[0], values[1], values[2]),
                center=tuple(values[3:6]),
                intrinsics=intrinsics[group_index],
            )
        )
    return cameras


def read_points(path: Path) -> list[tuple[float, float, float]]:
    return [tuple(map(float, line.split())) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_feature(path: Path) -> list[list[tuple[int, float, float]]]:
    tracks = []
    for line in path.read_text(encoding="utf-8").splitlines():
        tokens = line.split()
        track_len = int(tokens[0])
        observations = []
        for index in range(track_len):
            observations.append(
                (
                    int(tokens[1 + 3 * index]),
                    float(tokens[2 + 3 * index]),
                    float(tokens[3 + 3 * index]),
                )
            )
        tracks.append(observations)
    return tracks


def project(point: tuple[float, float, float], camera: Camera) -> tuple[float, float]:
    vector = tuple(point[index] - camera.center[index] for index in range(3))
    x_cam, y_cam, z_cam = mat_vec(camera.rotation, vector)
    fx, fy, cx, cy = camera.intrinsics
    return fx * x_cam / z_cam + cx, fy * y_cam / z_cam + cy


def collect_observations(
    points: list[tuple[float, float, float]],
    tracks: list[list[tuple[int, float, float]]],
    cameras: list[Camera],
    rng: random.Random,
) -> tuple[list[ObservationRef], float]:
    refs = []
    sum_squared = 0.0
    for point_index, (point, observations) in enumerate(zip(points, tracks)):
        for observation_index, (image_index, observed_u, observed_v) in enumerate(observations):
            projected_u, projected_v = project(point, cameras[image_index])
            residual = (projected_u - observed_u, projected_v - observed_v)
            noise = (rng.gauss(0.0, 1.0), rng.gauss(0.0, 1.0))
            refs.append(ObservationRef(point_index, observation_index, residual, noise))
            sum_squared += residual[0] * residual[0] + residual[1] * residual[1]
    return refs, math.sqrt(sum_squared / len(refs))


def mean_focal(cameras: list[Camera]) -> float:
    return sum((camera.intrinsics[0] + camera.intrinsics[1]) * 0.5 for camera in cameras) / len(cameras)


def solve_noise_scale(refs: list[ObservationRef], target_rmse: float) -> float:
    count = len(refs)
    a = sum(ref.noise[0] * ref.noise[0] + ref.noise[1] * ref.noise[1] for ref in refs)
    b = sum(ref.residual[0] * ref.noise[0] + ref.residual[1] * ref.noise[1] for ref in refs)
    c = sum(ref.residual[0] * ref.residual[0] + ref.residual[1] * ref.residual[1] for ref in refs)
    discriminant = max(0.0, b * b - a * (c - target_rmse * target_rmse * count))
    return (b + math.sqrt(discriminant)) / a


def write_feature(path: Path, tracks: list[list[tuple[int, float, float]]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for observations in tracks:
            fields = [str(len(observations))]
            for image_index, u, v in observations:
                fields.extend((str(image_index), f"{u:.12f}", f"{v:.12f}"))
            fh.write(" ".join(fields))
            fh.write("\n")


def write_noisy_gcp_observations(path: Path, output_path: Path, scale: float, rng: random.Random) -> int:
    if not path.exists():
        return 0
    count = 0
    with path.open(encoding="utf-8") as src, output_path.open("w", encoding="utf-8", newline="\n") as dst:
        for line in src:
            if not line.strip() or line.startswith("#"):
                dst.write(line)
                continue
            tokens = line.split()
            track_len = int(tokens[1])
            fields = tokens[:2]
            for index in range(track_len):
                image_index = tokens[2 + 3 * index]
                u = float(tokens[3 + 3 * index]) + scale * rng.gauss(0.0, 1.0)
                v = float(tokens[4 + 3 * index]) + scale * rng.gauss(0.0, 1.0)
                fields.extend((image_index, f"{u:.12f}", f"{v:.12f}"))
                count += 1
            dst.write(" ".join(fields))
            dst.write("\n")
    return count


def dataset_slug(prefix: str, image_count: int, point_count: int, observation_count: int, gcp_count: int, rmse: float | None = None) -> str:
    base = f"{prefix}-i{image_count}-p{point_count}-o{observation_count}-g{gcp_count}"
    if rmse is None:
        return base
    return f"{base}-init-rmse{rmse:06.2f}px".replace(".", "p")


def count_gcps(input_dir: Path) -> int:
    path = input_dir / "gcp.txt"
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#"))


def copy_dataset(input_dir: Path, output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    shutil.copytree(input_dir, output_dir)


def main() -> None:
    args = parse_args()
    rng = random.Random(args.seed)
    cameras = read_cameras(args.input_dir)
    points = read_points(args.input_dir / "XYZ.txt")
    tracks = read_feature(args.input_dir / "Feature.txt")
    refs, base_rmse = collect_observations(points, tracks, cameras, rng)
    observation_count = len(refs)
    gcp_count = count_gcps(args.input_dir)
    focal = mean_focal(cameras)
    base_name = dataset_slug(args.prefix, len(cameras), len(points), observation_count, gcp_count)
    print(f"base dataset name: {base_name}")
    print(f"base rmse: {base_rmse:.6f} px")

    args.output_root.mkdir(parents=True, exist_ok=True)
    for target_rmse in target_rmse_values(args):
        if target_rmse <= base_rmse:
            raise ValueError(f"Target RMSE {target_rmse} must be larger than base RMSE {base_rmse:.6f}")
        local_rng = random.Random(f"{args.seed}:{target_rmse}")
        local_refs, _ = collect_observations(points, tracks, cameras, local_rng)
        scale = solve_noise_scale(local_refs, target_rmse)
        noisy_tracks = [list(track) for track in tracks]
        for ref in local_refs:
            image_index, u, v = noisy_tracks[ref.point_index][ref.observation_index]
            noisy_tracks[ref.point_index][ref.observation_index] = (
                image_index,
                u + scale * ref.noise[0],
                v + scale * ref.noise[1],
            )
        output_dir = args.output_root / dataset_slug(args.prefix, len(cameras), len(points), observation_count, gcp_count, target_rmse)
        copy_dataset(args.input_dir, output_dir)
        write_feature(output_dir / "Feature.txt", noisy_tracks)
        noisy_gcp_observations = 0
        if args.include_gcp_observations:
            noisy_gcp_observations = write_noisy_gcp_observations(
                args.input_dir / "gcp_observations.txt",
                output_dir / "gcp_observations.txt",
                scale,
                local_rng,
            )
        metadata = {
            "source": str(args.input_dir.resolve()),
            "dataset_name": output_dir.name,
            "base_rmse_px": base_rmse,
            "base_rmse_normalized": base_rmse / focal,
            "target_rmse_px": target_rmse,
            "target_rmse_normalized": target_rmse / focal,
            "noise_model": "isotropic Gaussian image-observation noise, globally scaled to target tie-point RMSE",
            "noise_scale_px": scale,
            "noise_scale_normalized": scale / focal,
            "seed": args.seed,
            "images": len(cameras),
            "points": len(points),
            "observations": observation_count,
            "gcps": gcp_count,
            "gcp_observations_perturbed": noisy_gcp_observations,
        }
        (output_dir / "noise_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        print(f"wrote {output_dir} scale={scale:.6f} px")


if __name__ == "__main__":
    main()
