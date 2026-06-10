#!/usr/bin/env python3
"""Generate target-RMSE noisy PVL-BA datasets."""

from __future__ import annotations

import argparse
import json
import math
import os
import random
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Camera:
    rotation: list[list[float]]
    center: tuple[float, float, float]
    intrinsics: tuple[float, float, float, float]
    group_id: int


@dataclass(frozen=True)
class PoseNoise:
    rotation: tuple[float, float, float]
    translation: tuple[float, float, float]


@dataclass(frozen=True)
class PointNoise:
    position: tuple[float, float, float]


@dataclass(frozen=True)
class ObservationRef:
    point_index: int
    observation_index: int
    residual: tuple[float, float]
    noise: tuple[float, float]


@dataclass(frozen=True)
class GeometryStats:
    rmse: float
    points: int
    observations: int
    point_error_summary: dict | None = None


@dataclass
class ErrorAccumulator:
    sample_step: int = 1
    count: int = 0
    total: float = 0.0
    sum_squared: float = 0.0
    max_value: float = 0.0
    samples: list[float] | None = None

    def add(self, value: float, index: int) -> None:
        self.count += 1
        self.total += value
        self.sum_squared += value * value
        self.max_value = max(self.max_value, value)
        if self.samples is not None and index % max(1, self.sample_step) == 0:
            self.samples.append(value)

    def to_summary(self) -> dict[str, float]:
        if self.count == 0:
            return {"mean": 0.0, "rmse": 0.0, "median": 0.0, "p95": 0.0, "max": 0.0, "sample_count": 0}
        sampled = sorted(self.samples or [])
        if sampled:
            median = sampled[len(sampled) // 2]
            p95 = sampled[min(len(sampled) - 1, int(0.95 * len(sampled)))]
        else:
            median = 0.0
            p95 = 0.0
        return {
            "mean": self.total / self.count,
            "rmse": math.sqrt(self.sum_squared / self.count),
            "median": median,
            "p95": p95,
            "max": self.max_value,
            "sample_count": len(sampled),
        }


@dataclass(frozen=True)
class ScaleCandidate:
    scale: float
    rmse: float
    cameras: list[Camera]


MAIN_RMSE_PRESET = (2.0, 5.0, 10.0, 20.0, 50.0, 100.0)
STRESS_RMSE_PRESET = (200.0, 500.0)
QUALITY_MODES = ("auto", "init-pose-triangulate", "init-pose-point", "observation-noise")
MASK64 = (1 << 64) - 1


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
    parser.add_argument(
        "--mode",
        choices=QUALITY_MODES,
        default="auto",
        help="Quality mode. 'auto' uses pose triangulation for main levels and pose+point perturbation for stress levels.",
    )
    parser.add_argument("--rotation-weight-deg", type=float, default=1.0, help="Rotation noise at unit scale, in degrees")
    parser.add_argument("--translation-weight", type=float, default=1.0, help="Translation noise at unit scale, in world units")
    parser.add_argument("--point-weight", type=float, default=1.0, help="3D point noise at unit scale, in world units")
    parser.add_argument("--max-scale", type=float, default=1.0, help="Initial upper scale for pose-noise bisection")
    parser.add_argument("--bisection-iterations", type=int, default=24)
    parser.add_argument("--scale-solver", choices=("sampled", "exact"), default="sampled")
    parser.add_argument("--sample-max-points", type=int, default=50000, help="Maximum points used for sampled pose-scale solving")
    parser.add_argument("--full-refine-iterations", type=int, default=12, help="Full-dataset bisection refinements after sampled solving")
    parser.add_argument("--full-refine-tolerance", type=float, default=0.02, help="Skip full refine bisection when sampled scale is within this relative RMSE error")
    parser.add_argument("--full-search-steps", type=int, default=0, help="Local full-dataset scale probes for non-monotonic stress cases")
    parser.add_argument(
        "--pose-scale-override",
        action="append",
        default=[],
        metavar="TARGET=SCALE",
        help="Use an explicit pose-noise scale for a target RMSE, for example 500=0.894986",
    )
    parser.add_argument("--error-sample-max-points", type=int, default=100000, help="Maximum point-error samples stored in metadata")
    parser.add_argument("--static-file-policy", choices=("hardlink", "copy"), default="hardlink")
    parser.add_argument("--include-gcp-observations", action="store_true", help="Also perturb gcp_observations.txt")
    args = parser.parse_args()
    args.pose_scale_overrides = parse_pose_scale_overrides(args.pose_scale_override)
    return args


def parse_pose_scale_overrides(values: list[str]) -> dict[float, float]:
    overrides: dict[float, float] = {}
    for value in values:
        if "=" not in value:
            raise argparse.ArgumentTypeError(f"expected TARGET=SCALE for --pose-scale-override, got {value!r}")
        target_text, scale_text = value.split("=", 1)
        try:
            target = float(target_text)
            scale = float(scale_text)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"invalid --pose-scale-override {value!r}") from exc
        if target <= 0.0 or scale < 0.0:
            raise argparse.ArgumentTypeError(f"invalid --pose-scale-override {value!r}")
        overrides[target] = scale
    return overrides


def pose_scale_override_for(args: argparse.Namespace, target_rmse: float) -> float | None:
    for target, scale in args.pose_scale_overrides.items():
        if math.isclose(target, target_rmse, rel_tol=1e-9, abs_tol=1e-9):
            return scale
    return None


def target_rmse_values(args: argparse.Namespace) -> list[float]:
    if args.target_rmse:
        return args.target_rmse
    if args.preset == "main":
        return list(MAIN_RMSE_PRESET)
    if args.preset == "stress":
        return list(STRESS_RMSE_PRESET)
    return list(MAIN_RMSE_PRESET + STRESS_RMSE_PRESET)


def resolved_mode_for_target(args: argparse.Namespace, target_rmse: float) -> str:
    if args.mode != "auto":
        return args.mode
    return "init-pose-point" if target_rmse >= min(STRESS_RMSE_PRESET) else "init-pose-triangulate"


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
                group_id=group_index + 1,
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


def parse_feature_line(line: str) -> list[tuple[int, float, float]]:
    tokens = line.split()
    track_len = int(tokens[0])
    return [
        (
            int(tokens[1 + 3 * index]),
            float(tokens[2 + 3 * index]),
            float(tokens[3 + 3 * index]),
        )
        for index in range(track_len)
    ]


def iter_tracks(input_dir: Path):
    with (input_dir / "XYZ.txt").open(encoding="utf-8") as xyz_fh, (input_dir / "Feature.txt").open(
        encoding="utf-8"
    ) as feature_fh:
        for point_index, (xyz_line, feature_line) in enumerate(zip(xyz_fh, feature_fh)):
            if not xyz_line.strip() or not feature_line.strip():
                continue
            yield point_index, tuple(map(float, xyz_line.split())), parse_feature_line(feature_line)


def count_points_observations(input_dir: Path) -> tuple[int, int]:
    point_count = 0
    observation_count = 0
    with (input_dir / "Feature.txt").open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            point_count += 1
            observation_count += int(line.split(maxsplit=1)[0])
    return point_count, observation_count


def sample_step_for_points(point_count: int, max_samples: int) -> int:
    if max_samples <= 0:
        return 1
    return max(1, math.ceil(point_count / max_samples))


def should_use_sample(point_index: int, sample_step: int) -> bool:
    return point_index % max(1, sample_step) == 0


def project(point: tuple[float, float, float], camera: Camera) -> tuple[float, float]:
    vector = tuple(point[index] - camera.center[index] for index in range(3))
    x_cam, y_cam, z_cam = mat_vec(camera.rotation, vector)
    fx, fy, cx, cy = camera.intrinsics
    return fx * x_cam / z_cam + cx, fy * y_cam / z_cam + cy


def transpose(matrix: list[list[float]]) -> list[list[float]]:
    return [[matrix[row][col] for row in range(3)] for col in range(3)]


def mat_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[row][k] * b[k][col] for k in range(3)) for col in range(3)] for row in range(3)]


def rotation_x(angle: float) -> list[list[float]]:
    c = math.cos(angle)
    s = math.sin(angle)
    return [[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]]


def rotation_y(angle: float) -> list[list[float]]:
    c = math.cos(angle)
    s = math.sin(angle)
    return [[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]]


def rotation_z(angle: float) -> list[list[float]]:
    c = math.cos(angle)
    s = math.sin(angle)
    return [[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]]


def apply_pose_noise(cameras: list[Camera], noises: list[PoseNoise], scale: float, rotation_weight_deg: float, translation_weight: float) -> list[Camera]:
    rotation_weight = math.radians(rotation_weight_deg)
    noisy_cameras = []
    for camera, noise in zip(cameras, noises):
        rx = rotation_x(scale * rotation_weight * noise.rotation[0])
        ry = rotation_y(scale * rotation_weight * noise.rotation[1])
        rz = rotation_z(scale * rotation_weight * noise.rotation[2])
        delta = mat_mul(rz, mat_mul(ry, rx))
        noisy_rotation = mat_mul(delta, camera.rotation)
        noisy_center = tuple(camera.center[index] + scale * translation_weight * noise.translation[index] for index in range(3))
        noisy_cameras.append(Camera(noisy_rotation, noisy_center, camera.intrinsics, camera.group_id))
    return noisy_cameras


def make_pose_noises(camera_count: int, rng: random.Random) -> list[PoseNoise]:
    return [
        PoseNoise(
            rotation=(rng.gauss(0.0, 1.0), rng.gauss(0.0, 1.0), rng.gauss(0.0, 1.0)),
            translation=(rng.gauss(0.0, 1.0), rng.gauss(0.0, 1.0), rng.gauss(0.0, 1.0)),
        )
        for _ in range(camera_count)
    ]


def splitmix64(value: int) -> int:
    value = (value + 0x9E3779B97F4A7C15) & MASK64
    value = ((value ^ (value >> 30)) * 0xBF58476D1CE4E5B9) & MASK64
    value = ((value ^ (value >> 27)) * 0x94D049BB133111EB) & MASK64
    return (value ^ (value >> 31)) & MASK64


def uniform01_from_u64(value: int) -> float:
    return ((value >> 11) + 1) / ((1 << 53) + 1)


def normal_pair(seed: int, point_index: int, lane: int) -> tuple[float, float]:
    mixed = (int(seed) & MASK64) ^ ((point_index + 1) * 0x9E3779B97F4A7C15) ^ (lane * 0xD1B54A32D192ED03)
    u1 = uniform01_from_u64(splitmix64(mixed))
    u2 = uniform01_from_u64(splitmix64(mixed ^ 0x94D049BB133111EB))
    radius = math.sqrt(-2.0 * math.log(u1))
    angle = 2.0 * math.pi * u2
    return radius * math.cos(angle), radius * math.sin(angle)


def point_noise_for(seed: int, point_index: int) -> PointNoise:
    x, y = normal_pair(seed, point_index, 0)
    z, _ = normal_pair(seed, point_index, 1)
    return PointNoise((x, y, z))


def apply_point_noise(point: tuple[float, float, float], noise: PointNoise, scale: float, point_weight: float) -> tuple[float, float, float]:
    return tuple(point[index] + scale * point_weight * noise.position[index] for index in range(3))


def ray_from_observation(camera: Camera, u: float, v: float) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    fx, fy, cx, cy = camera.intrinsics
    direction_camera = ((u - cx) / fx, (v - cy) / fy, 1.0)
    direction_world = mat_vec(transpose(camera.rotation), direction_camera)
    norm = math.sqrt(sum(value * value for value in direction_world))
    return camera.center, tuple(value / norm for value in direction_world)


def solve_3x3(a: list[list[float]], b: list[float]) -> tuple[float, float, float] | None:
    rows = [[a[i][0], a[i][1], a[i][2], b[i]] for i in range(3)]
    for col in range(3):
        pivot = max(range(col, 3), key=lambda row: abs(rows[row][col]))
        if abs(rows[pivot][col]) < 1e-12:
            return None
        rows[col], rows[pivot] = rows[pivot], rows[col]
        factor = rows[col][col]
        for j in range(col, 4):
            rows[col][j] /= factor
        for row in range(3):
            if row == col:
                continue
            factor = rows[row][col]
            for j in range(col, 4):
                rows[row][j] -= factor * rows[col][j]
    return rows[0][3], rows[1][3], rows[2][3]


def triangulate_track(observations: list[tuple[int, float, float]], cameras: list[Camera], fallback: tuple[float, float, float]) -> tuple[float, float, float]:
    a = [[0.0, 0.0, 0.0] for _ in range(3)]
    b = [0.0, 0.0, 0.0]
    for image_index, u, v in observations:
        center, direction = ray_from_observation(cameras[image_index], u, v)
        projector = [
            [1.0 - direction[0] * direction[0], -direction[0] * direction[1], -direction[0] * direction[2]],
            [-direction[1] * direction[0], 1.0 - direction[1] * direction[1], -direction[1] * direction[2]],
            [-direction[2] * direction[0], -direction[2] * direction[1], 1.0 - direction[2] * direction[2]],
        ]
        for row in range(3):
            for col in range(3):
                a[row][col] += projector[row][col]
            b[row] += sum(projector[row][col] * center[col] for col in range(3))
    solved = solve_3x3(a, b)
    return fallback if solved is None else solved


def triangulate_points(
    tracks: list[list[tuple[int, float, float]]],
    cameras: list[Camera],
    fallback_points: list[tuple[float, float, float]],
) -> list[tuple[float, float, float]]:
    return [triangulate_track(track, cameras, fallback_points[index]) for index, track in enumerate(tracks)]


def rmse_for_geometry(points: list[tuple[float, float, float]], tracks: list[list[tuple[int, float, float]]], cameras: list[Camera]) -> float:
    sum_squared = 0.0
    count = 0
    for point, observations in zip(points, tracks):
        for image_index, observed_u, observed_v in observations:
            projected_u, projected_v = project(point, cameras[image_index])
            sum_squared += (projected_u - observed_u) ** 2 + (projected_v - observed_v) ** 2
            count += 1
    return math.sqrt(sum_squared / count)


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


def euler_from_rotation(rotation: list[list[float]]) -> tuple[float, float, float]:
    ex = math.asin(max(-1.0, min(1.0, -rotation[2][1])))
    c2 = math.cos(ex)
    if abs(c2) > 1e-12:
        ey = math.atan2(-rotation[2][0], rotation[2][2])
        ez = math.atan2(rotation[0][1], rotation[1][1])
    else:
        ey = 0.0
        ez = math.atan2(-rotation[1][0], rotation[0][0])
    return ey, ex, ez


def rotation_delta_deg(reference: list[list[float]], perturbed: list[list[float]]) -> float:
    delta = mat_mul(perturbed, transpose(reference))
    trace = delta[0][0] + delta[1][1] + delta[2][2]
    cos_theta = max(-1.0, min(1.0, (trace - 1.0) * 0.5))
    return math.degrees(math.acos(cos_theta))


def summary(values: list[float]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "rmse": 0.0, "median": 0.0, "p95": 0.0, "max": 0.0}
    ordered = sorted(values)
    count = len(ordered)
    return {
        "mean": sum(ordered) / count,
        "rmse": math.sqrt(sum(value * value for value in ordered) / count),
        "median": ordered[count // 2],
        "p95": ordered[min(count - 1, int(0.95 * count))],
        "max": ordered[-1],
    }


def pose_error_summary(reference: list[Camera], perturbed: list[Camera]) -> dict:
    center_errors = []
    rotation_errors = []
    for ref_camera, perturbed_camera in zip(reference, perturbed):
        center_errors.append(
            math.sqrt(
                sum((perturbed_camera.center[index] - ref_camera.center[index]) ** 2 for index in range(3))
            )
        )
        rotation_errors.append(rotation_delta_deg(ref_camera.rotation, perturbed_camera.rotation))
    return {
        "camera_center_error_world": summary(center_errors),
        "camera_rotation_error_deg": summary(rotation_errors),
    }


def point_error_summary(reference: list[tuple[float, float, float]], perturbed: list[tuple[float, float, float]]) -> dict:
    errors = [
        math.sqrt(sum((perturbed_point[index] - ref_point[index]) ** 2 for index in range(3)))
        for ref_point, perturbed_point in zip(reference, perturbed)
    ]
    return {"tie_point_error_world": summary(errors)}


def write_cam(path: Path, cameras: list[Camera]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for camera in cameras:
            ey, ex, ez = euler_from_rotation(camera.rotation)
            cx, cy, cz = camera.center
            fh.write(
                f"{ey:.12f} {ex:.12f} {ez:.12f} "
                f"{cx:.12f} {cy:.12f} {cz:.12f} {camera.group_id}\n"
            )


def write_xyz(path: Path, points: list[tuple[float, float, float]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for x, y, z in points:
            fh.write(f"{x:.12f} {y:.12f} {z:.12f}\n")


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


def quality_tag(mode: str) -> str:
    if mode == "init-pose-triangulate":
        return "init-rmse"
    if mode == "init-pose-point":
        return "joint-rmse"
    if mode == "observation-noise":
        return "obs-rmse"
    raise ValueError(f"Unsupported quality mode: {mode}")


def dataset_slug(
    prefix: str,
    image_count: int,
    point_count: int,
    observation_count: int,
    gcp_count: int,
    rmse: float | None = None,
    mode: str = "init-pose-triangulate",
) -> str:
    base = f"{prefix}-i{image_count}-p{point_count}-o{observation_count}-g{gcp_count}"
    if rmse is None:
        return base
    return f"{base}-{quality_tag(mode)}{rmse:06.2f}px".replace(".", "p")


def count_gcps(input_dir: Path) -> int:
    obs_path = input_dir / "gcp_observations.txt"
    if obs_path.exists():
        count = 0
        for line in obs_path.read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            tokens = line.split()
            if int(tokens[1]) > 0:
                count += 1
        return count
    path = input_dir / "gcp.txt"
    if not path.exists():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#"))


def target_relative_error(rmse: float, target_rmse: float) -> float:
    return abs(rmse - target_rmse) / target_rmse if target_rmse > 0.0 else 0.0


def variant_complete(
    path: Path,
    target_rmse: float | None = None,
    tolerance: float | None = None,
    mode: str | None = None,
) -> bool:
    metadata_path = path / "noise_metadata.json"
    complete = (
        (path / "cal.txt").exists()
        and (path / "Feature.txt").exists()
        and (path / "XYZ.txt").exists()
        and metadata_path.exists()
        and any(path.glob("Cam-*.txt"))
    )
    if not complete or target_rmse is None or tolerance is None:
        return complete
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if mode is not None and metadata.get("mode") != mode:
            return False
        actual_rmse = float(metadata["actual_rmse_px"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False
    return target_relative_error(actual_rmse, target_rmse) <= tolerance


def copy_dataset(input_dir: Path, output_dir: Path) -> None:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    shutil.copytree(input_dir, output_dir)


def link_or_copy_file(source: Path, target: Path, policy: str) -> None:
    if policy == "hardlink":
        try:
            os.link(source, target)
            return
        except OSError:
            pass
    shutil.copy2(source, target)


def prepare_static_dataset(input_dir: Path, output_dir: Path, policy: str) -> Path:
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cam_output: Path | None = None
    for source in input_dir.iterdir():
        if source.is_dir():
            shutil.copytree(source, output_dir / source.name)
            continue
        target = output_dir / source.name
        if source.name == "XYZ.txt":
            continue
        if source.name.startswith("Cam") and source.suffix == ".txt":
            cam_output = target
            continue
        link_or_copy_file(source, target, policy)
    if cam_output is None:
        cam_output = output_dir / find_cam_file(input_dir).name
    return cam_output


def make_base_metadata(
    args: argparse.Namespace,
    output_dir: Path,
    cameras: list[Camera],
    point_count: int,
    observation_count: int,
    gcp_count: int,
    focal: float,
    base_rmse: float,
    target_rmse: float,
    mode: str,
) -> dict:
    metadata = {
        "source": str(args.input_dir.resolve()),
        "dataset_name": output_dir.name,
        "mode": mode,
        "base_rmse_px": base_rmse,
        "base_rmse_normalized": base_rmse / focal,
        "target_rmse_px": target_rmse,
        "target_rmse_normalized": target_rmse / focal,
        "seed": args.seed,
        "images": len(cameras),
        "points": point_count,
        "observations": observation_count,
        "gcps": gcp_count,
    }
    source_metadata_path = args.input_dir / "dataset_metadata.json"
    if source_metadata_path.exists():
        try:
            source_metadata = json.loads(source_metadata_path.read_text(encoding="utf-8"))
            for key in ("source_software_vendor", "source_software"):
                if source_metadata.get(key):
                    metadata[key] = source_metadata[key]
        except (OSError, json.JSONDecodeError, TypeError):
            pass
    return metadata


def write_metadata(path: Path, metadata: dict) -> None:
    path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def generate_observation_noise_variant(
    args: argparse.Namespace,
    output_dir: Path,
    cameras: list[Camera],
    points: list[tuple[float, float, float]],
    tracks: list[list[tuple[int, float, float]]],
    observation_count: int,
    gcp_count: int,
    focal: float,
    base_rmse: float,
    target_rmse: float,
) -> None:
    local_rng = random.Random(f"{args.seed}:{target_rmse}:observation")
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
    metadata = make_base_metadata(
        args,
        output_dir,
        cameras,
        len(points),
        observation_count,
        gcp_count,
        focal,
        base_rmse,
        target_rmse,
        "observation-noise",
    )
    metadata.update(
        {
            "generation_strategy": "image observation noise",
            "noise_model": "isotropic Gaussian image-observation noise, globally scaled to target tie-point RMSE",
            "noise_scale_px": scale,
            "noise_scale_normalized": scale / focal,
            "actual_rmse_px": rmse_for_geometry(points, noisy_tracks, cameras),
            "gcp_observations_perturbed": noisy_gcp_observations,
        }
    )
    write_metadata(output_dir / "noise_metadata.json", metadata)
    print(f"wrote {output_dir} observation_noise_scale={scale:.6f} px")


def geometry_for_pose_scale(
    cameras: list[Camera],
    points: list[tuple[float, float, float]],
    tracks: list[list[tuple[int, float, float]]],
    pose_noises: list[PoseNoise],
    scale: float,
    rotation_weight_deg: float,
    translation_weight: float,
) -> tuple[list[Camera], list[tuple[float, float, float]], float]:
    noisy_cameras = apply_pose_noise(cameras, pose_noises, scale, rotation_weight_deg, translation_weight)
    triangulated_points = triangulate_points(tracks, noisy_cameras, points)
    rmse = rmse_for_geometry(triangulated_points, tracks, noisy_cameras)
    return noisy_cameras, triangulated_points, rmse


def stream_geometry_for_pose_scale(
    input_dir: Path,
    cameras: list[Camera],
    pose_noises: list[PoseNoise],
    scale: float,
    rotation_weight_deg: float,
    translation_weight: float,
    sample_step: int = 1,
    xyz_output: Path | None = None,
    include_point_errors: bool = False,
    point_error_sample_step: int = 1,
) -> tuple[list[Camera], GeometryStats]:
    noisy_cameras = apply_pose_noise(cameras, pose_noises, scale, rotation_weight_deg, translation_weight)
    sum_squared = 0.0
    observation_count = 0
    point_count = 0
    error_accumulator = ErrorAccumulator(
        sample_step=point_error_sample_step,
        samples=[] if include_point_errors else None,
    )
    xyz_fh = xyz_output.open("w", encoding="utf-8", newline="\n") if xyz_output is not None else None
    try:
        for point_index, fallback_point, observations in iter_tracks(input_dir):
            if not should_use_sample(point_index, sample_step):
                continue
            point = triangulate_track(observations, noisy_cameras, fallback_point)
            if xyz_fh is not None:
                x, y, z = point
                xyz_fh.write(f"{x:.12f} {y:.12f} {z:.12f}\n")
            if include_point_errors:
                error = math.sqrt(sum((point[index] - fallback_point[index]) ** 2 for index in range(3)))
                error_accumulator.add(error, point_index)
            point_count += 1
            for image_index, observed_u, observed_v in observations:
                projected_u, projected_v = project(point, noisy_cameras[image_index])
                sum_squared += (projected_u - observed_u) ** 2 + (projected_v - observed_v) ** 2
                observation_count += 1
    finally:
        if xyz_fh is not None:
            xyz_fh.close()
    rmse = math.sqrt(sum_squared / observation_count) if observation_count else 0.0
    return noisy_cameras, GeometryStats(
        rmse=rmse,
        points=point_count,
        observations=observation_count,
        point_error_summary={"tie_point_error_world": error_accumulator.to_summary()} if include_point_errors else None,
    )


def rmse_for_input_stream(input_dir: Path, cameras: list[Camera], sample_step: int = 1) -> GeometryStats:
    sum_squared = 0.0
    observation_count = 0
    point_count = 0
    for point_index, point, observations in iter_tracks(input_dir):
        if not should_use_sample(point_index, sample_step):
            continue
        point_count += 1
        for image_index, observed_u, observed_v in observations:
            projected_u, projected_v = project(point, cameras[image_index])
            sum_squared += (projected_u - observed_u) ** 2 + (projected_v - observed_v) ** 2
            observation_count += 1
    rmse = math.sqrt(sum_squared / observation_count) if observation_count else 0.0
    return GeometryStats(rmse=rmse, points=point_count, observations=observation_count)


def stream_geometry_for_pose_point_scale(
    input_dir: Path,
    cameras: list[Camera],
    pose_noises: list[PoseNoise],
    scale: float,
    rotation_weight_deg: float,
    translation_weight: float,
    point_weight: float,
    seed: int,
    sample_step: int = 1,
    xyz_output: Path | None = None,
    include_point_errors: bool = False,
    point_error_sample_step: int = 1,
) -> tuple[list[Camera], GeometryStats]:
    noisy_cameras = apply_pose_noise(cameras, pose_noises, scale, rotation_weight_deg, translation_weight)
    sum_squared = 0.0
    observation_count = 0
    point_count = 0
    error_accumulator = ErrorAccumulator(
        sample_step=point_error_sample_step,
        samples=[] if include_point_errors else None,
    )
    xyz_fh = xyz_output.open("w", encoding="utf-8", newline="\n") if xyz_output is not None else None
    try:
        for point_index, fallback_point, observations in iter_tracks(input_dir):
            if not should_use_sample(point_index, sample_step):
                continue
            point = apply_point_noise(fallback_point, point_noise_for(seed, point_index), scale, point_weight)
            if xyz_fh is not None:
                x, y, z = point
                xyz_fh.write(f"{x:.12f} {y:.12f} {z:.12f}\n")
            if include_point_errors:
                error = math.sqrt(sum((point[index] - fallback_point[index]) ** 2 for index in range(3)))
                error_accumulator.add(error, point_index)
            point_count += 1
            for image_index, observed_u, observed_v in observations:
                projected_u, projected_v = project(point, noisy_cameras[image_index])
                sum_squared += (projected_u - observed_u) ** 2 + (projected_v - observed_v) ** 2
                observation_count += 1
    finally:
        if xyz_fh is not None:
            xyz_fh.close()
    rmse = math.sqrt(sum_squared / observation_count) if observation_count else 0.0
    return noisy_cameras, GeometryStats(
        rmse=rmse,
        points=point_count,
        observations=observation_count,
        point_error_summary={"tie_point_error_world": error_accumulator.to_summary()} if include_point_errors else None,
    )


def solve_pose_scale_streaming(
    args: argparse.Namespace,
    cameras: list[Camera],
    point_count: int,
    pose_noises: list[PoseNoise],
    target_rmse: float,
) -> tuple[float, list[Camera], float, int, int]:
    sample_step = 1 if args.scale_solver == "exact" else sample_step_for_points(point_count, args.sample_max_points)

    def relative_error(rmse: float) -> float:
        return target_relative_error(rmse, target_rmse)

    def better(candidate: ScaleCandidate, best: ScaleCandidate) -> bool:
        return relative_error(candidate.rmse) < relative_error(best.rmse)

    low = 0.0
    high = max(args.max_scale, 1e-9)
    high_cameras, high_stats = stream_geometry_for_pose_scale(
        args.input_dir,
        cameras,
        pose_noises,
        high,
        args.rotation_weight_deg,
        args.translation_weight,
        sample_step,
    )
    for _ in range(40):
        if high_stats.rmse >= target_rmse:
            break
        high *= 2.0
        high_cameras, high_stats = stream_geometry_for_pose_scale(
            args.input_dir,
            cameras,
            pose_noises,
            high,
            args.rotation_weight_deg,
            args.translation_weight,
            sample_step,
        )
    else:
        raise RuntimeError(f"Could not reach target RMSE {target_rmse} px with pose perturbation")

    best = ScaleCandidate(high, high_stats.rmse, high_cameras)
    for _ in range(args.bisection_iterations):
        mid = (low + high) * 0.5
        mid_cameras, mid_stats = stream_geometry_for_pose_scale(
            args.input_dir,
            cameras,
            pose_noises,
            mid,
            args.rotation_weight_deg,
            args.translation_weight,
            sample_step,
        )
        candidate = ScaleCandidate(mid, mid_stats.rmse, mid_cameras)
        if better(candidate, best):
            best = candidate
        if relative_error(best.rmse) <= args.full_refine_tolerance:
            break
        if mid_stats.rmse < target_rmse:
            low = mid
        else:
            high = mid

    full_refine_passes = 0
    if sample_step > 1 and args.full_refine_iterations > 0:
        best_scale, best_cameras, best_rmse, full_refine_passes = refine_pose_scale_full_stream(
            args,
            cameras,
            pose_noises,
            target_rmse,
            best.scale,
        )
    else:
        best_scale, best_cameras, best_rmse = best.scale, best.cameras, best.rmse

    return best_scale, best_cameras, best_rmse, sample_step, full_refine_passes


def refine_pose_scale_full_stream(
    args: argparse.Namespace,
    cameras: list[Camera],
    pose_noises: list[PoseNoise],
    target_rmse: float,
    initial_scale: float,
) -> tuple[float, list[Camera], float, int]:
    passes = 0

    def evaluate(scale: float) -> ScaleCandidate:
        nonlocal passes
        passes += 1
        candidate_cameras, stats = stream_geometry_for_pose_scale(
            args.input_dir,
            cameras,
            pose_noises,
            max(scale, 0.0),
            args.rotation_weight_deg,
            args.translation_weight,
            1,
        )
        return ScaleCandidate(max(scale, 0.0), stats.rmse, candidate_cameras)

    def relative_error(rmse: float) -> float:
        return target_relative_error(rmse, target_rmse)

    def better(candidate: ScaleCandidate, best: ScaleCandidate) -> bool:
        return relative_error(candidate.rmse) < relative_error(best.rmse)

    best = evaluate(initial_scale)
    best_error = relative_error(best.rmse)
    if best_error <= args.full_refine_tolerance:
        return best.scale, best.cameras, best.rmse, passes

    if best.rmse >= target_rmse:
        low = 0.0
        high = best.scale
    else:
        low = best.scale
        high = max(best.scale * 2.0, 1e-9)
        for _ in range(40):
            candidate = evaluate(high)
            high_error = relative_error(candidate.rmse)
            if better(candidate, best):
                best = candidate
                best_error = high_error
                if best_error <= args.full_refine_tolerance:
                    return best.scale, best.cameras, best.rmse, passes
            if candidate.rmse >= target_rmse:
                break
            low = high
            high *= 2.0
        else:
            return best.scale, best.cameras, best.rmse, passes

    for _ in range(args.full_refine_iterations):
        mid = (low + high) * 0.5
        candidate = evaluate(mid)
        mid_error = relative_error(candidate.rmse)
        if better(candidate, best):
            best = candidate
            best_error = mid_error
            if best_error <= args.full_refine_tolerance:
                break
        if candidate.rmse < target_rmse:
            low = mid
        else:
            high = mid

    if best_error <= args.full_refine_tolerance or args.full_search_steps <= 0:
        return best.scale, best.cameras, best.rmse, passes

    search_center = best.scale
    search_radius = max(abs(high - low), search_center * 0.01, 1e-6)
    for _ in range(max(1, args.full_refine_iterations)):
        steps = max(2, args.full_search_steps)
        improved = False
        for step_index in range(steps + 1):
            scale = search_center - search_radius + (2.0 * search_radius * step_index / steps)
            candidate = evaluate(scale)
            candidate_error = relative_error(candidate.rmse)
            if better(candidate, best):
                best = candidate
                best_error = candidate_error
                search_center = candidate.scale
                improved = True
                if best_error <= args.full_refine_tolerance:
                    return best.scale, best.cameras, best.rmse, passes
        search_radius *= 0.5
        if not improved and search_radius < max(search_center * 1e-6, 1e-8):
            break

    return best.scale, best.cameras, best.rmse, passes


def solve_pose_point_scale_streaming(
    args: argparse.Namespace,
    cameras: list[Camera],
    point_count: int,
    pose_noises: list[PoseNoise],
    target_rmse: float,
) -> tuple[float, list[Camera], float, int, int]:
    sample_step = 1 if args.scale_solver == "exact" else sample_step_for_points(point_count, args.sample_max_points)

    def relative_error(rmse: float) -> float:
        return target_relative_error(rmse, target_rmse)

    def better(candidate: ScaleCandidate, best: ScaleCandidate) -> bool:
        return relative_error(candidate.rmse) < relative_error(best.rmse)

    low = 0.0
    high = max(args.max_scale, 1e-9)
    high_cameras, high_stats = stream_geometry_for_pose_point_scale(
        args.input_dir,
        cameras,
        pose_noises,
        high,
        args.rotation_weight_deg,
        args.translation_weight,
        args.point_weight,
        args.seed,
        sample_step,
    )
    for _ in range(40):
        if high_stats.rmse >= target_rmse:
            break
        high *= 2.0
        high_cameras, high_stats = stream_geometry_for_pose_point_scale(
            args.input_dir,
            cameras,
            pose_noises,
            high,
            args.rotation_weight_deg,
            args.translation_weight,
            args.point_weight,
            args.seed,
            sample_step,
        )
    else:
        raise RuntimeError(f"Could not reach target RMSE {target_rmse} px with pose+point perturbation")

    best = ScaleCandidate(high, high_stats.rmse, high_cameras)
    for _ in range(args.bisection_iterations):
        mid = (low + high) * 0.5
        mid_cameras, mid_stats = stream_geometry_for_pose_point_scale(
            args.input_dir,
            cameras,
            pose_noises,
            mid,
            args.rotation_weight_deg,
            args.translation_weight,
            args.point_weight,
            args.seed,
            sample_step,
        )
        candidate = ScaleCandidate(mid, mid_stats.rmse, mid_cameras)
        if better(candidate, best):
            best = candidate
        if relative_error(best.rmse) <= args.full_refine_tolerance:
            break
        if mid_stats.rmse < target_rmse:
            low = mid
        else:
            high = mid

    full_refine_passes = 0
    if sample_step > 1 and args.full_refine_iterations > 0:
        best_scale, best_cameras, best_rmse, full_refine_passes = refine_pose_point_scale_full_stream(
            args,
            cameras,
            pose_noises,
            target_rmse,
            best.scale,
        )
    else:
        best_scale, best_cameras, best_rmse = best.scale, best.cameras, best.rmse

    return best_scale, best_cameras, best_rmse, sample_step, full_refine_passes


def refine_pose_point_scale_full_stream(
    args: argparse.Namespace,
    cameras: list[Camera],
    pose_noises: list[PoseNoise],
    target_rmse: float,
    initial_scale: float,
) -> tuple[float, list[Camera], float, int]:
    passes = 0

    def evaluate(scale: float) -> ScaleCandidate:
        nonlocal passes
        passes += 1
        candidate_cameras, stats = stream_geometry_for_pose_point_scale(
            args.input_dir,
            cameras,
            pose_noises,
            max(scale, 0.0),
            args.rotation_weight_deg,
            args.translation_weight,
            args.point_weight,
            args.seed,
            1,
        )
        return ScaleCandidate(max(scale, 0.0), stats.rmse, candidate_cameras)

    def relative_error(rmse: float) -> float:
        return target_relative_error(rmse, target_rmse)

    def better(candidate: ScaleCandidate, best: ScaleCandidate) -> bool:
        return relative_error(candidate.rmse) < relative_error(best.rmse)

    best = evaluate(initial_scale)
    best_error = relative_error(best.rmse)
    if best_error <= args.full_refine_tolerance:
        return best.scale, best.cameras, best.rmse, passes

    if best.rmse >= target_rmse:
        low = 0.0
        high = best.scale
    else:
        low = best.scale
        high = max(best.scale * 2.0, 1e-9)
        for _ in range(40):
            candidate = evaluate(high)
            high_error = relative_error(candidate.rmse)
            if better(candidate, best):
                best = candidate
                best_error = high_error
                if best_error <= args.full_refine_tolerance:
                    return best.scale, best.cameras, best.rmse, passes
            if candidate.rmse >= target_rmse:
                break
            low = high
            high *= 2.0
        else:
            return best.scale, best.cameras, best.rmse, passes

    for _ in range(args.full_refine_iterations):
        mid = (low + high) * 0.5
        candidate = evaluate(mid)
        mid_error = relative_error(candidate.rmse)
        if better(candidate, best):
            best = candidate
            best_error = mid_error
            if best_error <= args.full_refine_tolerance:
                break
        if candidate.rmse < target_rmse:
            low = mid
        else:
            high = mid

    if best_error <= args.full_refine_tolerance or args.full_search_steps <= 0:
        return best.scale, best.cameras, best.rmse, passes

    search_center = best.scale
    search_radius = max(abs(high - low), search_center * 0.01, 1e-6)
    for _ in range(max(1, args.full_refine_iterations)):
        steps = max(2, args.full_search_steps)
        improved = False
        for step_index in range(steps + 1):
            scale = search_center - search_radius + (2.0 * search_radius * step_index / steps)
            candidate = evaluate(scale)
            candidate_error = relative_error(candidate.rmse)
            if better(candidate, best):
                best = candidate
                best_error = candidate_error
                search_center = candidate.scale
                improved = True
                if best_error <= args.full_refine_tolerance:
                    return best.scale, best.cameras, best.rmse, passes
        search_radius *= 0.5
        if not improved and search_radius < max(search_center * 1e-6, 1e-8):
            break

    return best.scale, best.cameras, best.rmse, passes


def solve_pose_scale(
    args: argparse.Namespace,
    cameras: list[Camera],
    points: list[tuple[float, float, float]],
    tracks: list[list[tuple[int, float, float]]],
    pose_noises: list[PoseNoise],
    target_rmse: float,
) -> tuple[float, list[Camera], list[tuple[float, float, float]], float]:
    low = 0.0
    high = max(args.max_scale, 1e-9)
    high_cameras, high_points, high_rmse = geometry_for_pose_scale(
        cameras, points, tracks, pose_noises, high, args.rotation_weight_deg, args.translation_weight
    )
    for _ in range(40):
        if high_rmse >= target_rmse:
            break
        high *= 2.0
        high_cameras, high_points, high_rmse = geometry_for_pose_scale(
            cameras, points, tracks, pose_noises, high, args.rotation_weight_deg, args.translation_weight
        )
    else:
        raise RuntimeError(f"Could not reach target RMSE {target_rmse} px with pose perturbation")

    best_scale = high
    best_cameras = high_cameras
    best_points = high_points
    best_rmse = high_rmse
    for _ in range(args.bisection_iterations):
        mid = (low + high) * 0.5
        mid_cameras, mid_points, mid_rmse = geometry_for_pose_scale(
            cameras, points, tracks, pose_noises, mid, args.rotation_weight_deg, args.translation_weight
        )
        best_scale = mid
        best_cameras = mid_cameras
        best_points = mid_points
        best_rmse = mid_rmse
        if mid_rmse < target_rmse:
            low = mid
        else:
            high = mid
    return best_scale, best_cameras, best_points, best_rmse


def generate_pose_triangulate_variant(
    args: argparse.Namespace,
    output_dir: Path,
    cameras: list[Camera],
    point_count: int,
    observation_count: int,
    gcp_count: int,
    focal: float,
    base_rmse: float,
    target_rmse: float,
    pose_noises: list[PoseNoise],
) -> None:
    override_scale = pose_scale_override_for(args, target_rmse)
    sample_step = 1 if args.scale_solver == "exact" else sample_step_for_points(point_count, args.sample_max_points)
    if override_scale is None:
        scale, noisy_cameras, scale_rmse, sample_step, full_refine_passes = solve_pose_scale_streaming(
            args, cameras, point_count, pose_noises, target_rmse
        )
        scale_solver = args.scale_solver
    else:
        scale = override_scale
        noisy_cameras = apply_pose_noise(cameras, pose_noises, scale, args.rotation_weight_deg, args.translation_weight)
        scale_rmse = 0.0
        full_refine_passes = 0
        scale_solver = "override"
    cam_output = prepare_static_dataset(args.input_dir, output_dir, args.static_file_policy)
    noisy_cameras, final_stats = stream_geometry_for_pose_scale(
        args.input_dir,
        cameras,
        pose_noises,
        scale,
        args.rotation_weight_deg,
        args.translation_weight,
        1,
        output_dir / "XYZ.txt",
        include_point_errors=True,
        point_error_sample_step=sample_step_for_points(point_count, args.error_sample_max_points),
    )
    write_cam(cam_output, noisy_cameras)
    metadata = make_base_metadata(
        args,
        output_dir,
        cameras,
        point_count,
        observation_count,
        gcp_count,
        focal,
        base_rmse,
        target_rmse,
        "init-pose-triangulate",
    )
    metadata.update(
        {
            "generation_strategy": "camera pose perturbation followed by multi-ray least-squares triangulation",
            "pose_noise_scale": scale,
            "pose_noise_direction": "shared across target RMSE levels for this run",
            "rotation_weight_deg": args.rotation_weight_deg,
            "translation_weight": args.translation_weight,
            "scale_solver": scale_solver,
            "scale_solver_sample_step": sample_step,
            "scale_solver_rmse_px": final_stats.rmse if override_scale is not None else scale_rmse,
            "full_refine_iterations": args.full_refine_iterations,
            "full_refine_tolerance": args.full_refine_tolerance,
            "full_refine_passes": full_refine_passes,
            "pose_scale_override": override_scale,
            "static_file_policy": args.static_file_policy,
            "actual_rmse_px": final_stats.rmse,
            "actual_rmse_normalized": final_stats.rmse / focal,
            "target_relative_error": target_relative_error(final_stats.rmse, target_rmse),
            "target_tolerance": args.full_refine_tolerance,
            "target_within_tolerance": target_relative_error(final_stats.rmse, target_rmse) <= args.full_refine_tolerance,
            "triangulation": "least-squares point closest to all observation rays under perturbed cameras",
            "feature_observations_perturbed": 0,
            "gcp_observations_perturbed": 0,
        }
    )
    metadata.update(pose_error_summary(cameras, noisy_cameras))
    if final_stats.point_error_summary:
        metadata.update(final_stats.point_error_summary)
    write_metadata(output_dir / "noise_metadata.json", metadata)
    print(
        f"wrote {output_dir} pose_scale={scale:.6f} "
        f"solver_rmse={scale_rmse:.6f} actual_rmse={final_stats.rmse:.6f} px"
    )


def generate_pose_point_variant(
    args: argparse.Namespace,
    output_dir: Path,
    cameras: list[Camera],
    point_count: int,
    observation_count: int,
    gcp_count: int,
    focal: float,
    base_rmse: float,
    target_rmse: float,
    pose_noises: list[PoseNoise],
) -> None:
    override_scale = pose_scale_override_for(args, target_rmse)
    sample_step = 1 if args.scale_solver == "exact" else sample_step_for_points(point_count, args.sample_max_points)
    if override_scale is None:
        scale, noisy_cameras, scale_rmse, sample_step, full_refine_passes = solve_pose_point_scale_streaming(
            args, cameras, point_count, pose_noises, target_rmse
        )
        scale_solver = args.scale_solver
    else:
        scale = override_scale
        noisy_cameras = apply_pose_noise(cameras, pose_noises, scale, args.rotation_weight_deg, args.translation_weight)
        scale_rmse = 0.0
        full_refine_passes = 0
        scale_solver = "override"
    cam_output = prepare_static_dataset(args.input_dir, output_dir, args.static_file_policy)
    noisy_cameras, final_stats = stream_geometry_for_pose_point_scale(
        args.input_dir,
        cameras,
        pose_noises,
        scale,
        args.rotation_weight_deg,
        args.translation_weight,
        args.point_weight,
        args.seed,
        1,
        output_dir / "XYZ.txt",
        include_point_errors=True,
        point_error_sample_step=sample_step_for_points(point_count, args.error_sample_max_points),
    )
    write_cam(cam_output, noisy_cameras)
    metadata = make_base_metadata(
        args,
        output_dir,
        cameras,
        point_count,
        observation_count,
        gcp_count,
        focal,
        base_rmse,
        target_rmse,
        "init-pose-point",
    )
    metadata.update(
        {
            "generation_strategy": "joint camera-pose and 3D tie-point initialization perturbation",
            "noise_model": "shared-scale Gaussian camera-pose and 3D tie-point initialization noise",
            "pose_noise_scale": scale,
            "point_noise_scale_world": scale * args.point_weight,
            "pose_noise_direction": "shared across target RMSE levels for this run",
            "point_noise_direction": "deterministic per tie point from seed and point index",
            "rotation_weight_deg": args.rotation_weight_deg,
            "translation_weight": args.translation_weight,
            "point_weight": args.point_weight,
            "scale_solver": scale_solver,
            "scale_solver_sample_step": sample_step,
            "scale_solver_rmse_px": final_stats.rmse if override_scale is not None else scale_rmse,
            "full_refine_iterations": args.full_refine_iterations,
            "full_refine_tolerance": args.full_refine_tolerance,
            "full_refine_passes": full_refine_passes,
            "pose_scale_override": override_scale,
            "static_file_policy": args.static_file_policy,
            "actual_rmse_px": final_stats.rmse,
            "actual_rmse_normalized": final_stats.rmse / focal,
            "target_relative_error": target_relative_error(final_stats.rmse, target_rmse),
            "target_tolerance": args.full_refine_tolerance,
            "target_within_tolerance": target_relative_error(final_stats.rmse, target_rmse) <= args.full_refine_tolerance,
            "points_retriangulated": False,
            "feature_observations_perturbed": 0,
            "gcp_observations_perturbed": 0,
        }
    )
    metadata.update(pose_error_summary(cameras, noisy_cameras))
    if final_stats.point_error_summary:
        metadata.update(final_stats.point_error_summary)
    write_metadata(output_dir / "noise_metadata.json", metadata)
    print(
        f"wrote {output_dir} joint_scale={scale:.6f} "
        f"solver_rmse={scale_rmse:.6f} actual_rmse={final_stats.rmse:.6f} px"
    )


def main() -> None:
    args = parse_args()
    cameras = read_cameras(args.input_dir)
    point_count, observation_count = count_points_observations(args.input_dir)
    base_stats = rmse_for_input_stream(args.input_dir, cameras)
    base_rmse = base_stats.rmse
    gcp_count = count_gcps(args.input_dir)
    focal = mean_focal(cameras)
    base_name = dataset_slug(args.prefix, len(cameras), point_count, observation_count, gcp_count)
    print(f"base dataset name: {base_name}")
    print(f"base rmse: {base_rmse:.6f} px")

    args.output_root.mkdir(parents=True, exist_ok=True)
    targets = target_rmse_values(args)
    target_modes = [resolved_mode_for_target(args, target_rmse) for target_rmse in targets]
    points: list[tuple[float, float, float]] | None = None
    tracks: list[list[tuple[int, float, float]]] | None = None
    if any(mode == "observation-noise" for mode in target_modes):
        rng = random.Random(args.seed)
        points = read_points(args.input_dir / "XYZ.txt")
        tracks = read_feature(args.input_dir / "Feature.txt")
        refs, base_rmse = collect_observations(points, tracks, cameras, rng)
        observation_count = len(refs)
    pose_noises = make_pose_noises(len(cameras), random.Random(f"{args.seed}:pose"))
    for target_rmse, target_mode in zip(targets, target_modes):
        if target_rmse <= base_rmse:
            raise ValueError(f"Target RMSE {target_rmse} must be larger than base RMSE {base_rmse:.6f}")
        print(f"target rmse: {target_rmse:g} px ({target_mode})", flush=True)
        output_dir = args.output_root / dataset_slug(
            args.prefix,
            len(cameras),
            point_count,
            observation_count,
            gcp_count,
            target_rmse,
            target_mode,
        )
        if variant_complete(output_dir, target_rmse, args.full_refine_tolerance, target_mode):
            print(f"skip existing complete variant {output_dir}", flush=True)
            continue
        if target_mode == "observation-noise":
            assert points is not None and tracks is not None
            generate_observation_noise_variant(
                args,
                output_dir,
                cameras,
                points,
                tracks,
                observation_count,
                gcp_count,
                focal,
                base_rmse,
                target_rmse,
            )
        elif target_mode == "init-pose-point":
            generate_pose_point_variant(
                args,
                output_dir,
                cameras,
                point_count,
                observation_count,
                gcp_count,
                focal,
                base_rmse,
                target_rmse,
                pose_noises,
            )
        else:
            generate_pose_triangulate_variant(
                args,
                output_dir,
                cameras,
                point_count,
                observation_count,
                gcp_count,
                focal,
                base_rmse,
                target_rmse,
                pose_noises,
            )


if __name__ == "__main__":
    main()
