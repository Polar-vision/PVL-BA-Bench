#!/usr/bin/env python3
"""Generate an interactive HTML viewer for BA datasets."""

from __future__ import annotations

import argparse
import json
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
    qvec: tuple[float, float, float, float]
    tvec: tuple[float, float, float]
    camera_id: int
    name: str


@dataclass(frozen=True)
class Point3D:
    point3d_id: int
    xyz: tuple[float, float, float]
    rgb: tuple[int, int, int]
    track_len: int


@dataclass(frozen=True)
class Gcp:
    gcp_id: int
    name: str
    xyz: tuple[float, float, float]
    is_check_point: bool
    observation_count: int


@dataclass(frozen=True)
class PvlCamera:
    rotation: list[list[float]]
    center: tuple[float, float, float]
    intrinsics: tuple[float, float, float, float]
    width: int
    height: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", required=True, type=Path, help="COLMAP text model or PVL-BA directory")
    parser.add_argument("--output", required=True, type=Path, help="Output HTML file")
    parser.add_argument("--format", choices=("auto", "colmap", "pvl-ba"), default="auto")
    parser.add_argument("--max-points", type=int, default=100_000, help="Maximum sparse points to embed")
    parser.add_argument("--camera-stride", type=int, default=1, help="Embed every Nth camera frustum")
    return parser.parse_args()


def qvec_to_rotation(qvec: tuple[float, float, float, float]) -> list[list[float]]:
    qw, qx, qy, qz = qvec
    return [
        [1.0 - 2.0 * qy * qy - 2.0 * qz * qz, 2.0 * qx * qy - 2.0 * qz * qw, 2.0 * qx * qz + 2.0 * qy * qw],
        [2.0 * qx * qy + 2.0 * qz * qw, 1.0 - 2.0 * qx * qx - 2.0 * qz * qz, 2.0 * qy * qz - 2.0 * qx * qw],
        [2.0 * qx * qz - 2.0 * qy * qw, 2.0 * qy * qz + 2.0 * qx * qw, 1.0 - 2.0 * qx * qx - 2.0 * qy * qy],
    ]


def rotation_transpose_vec(rotation: list[list[float]], vector: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(sum(rotation[row][col] * vector[row] for row in range(3)) for col in range(3))


def camera_center(image: Image) -> tuple[float, float, float]:
    rotation = qvec_to_rotation(image.qvec)
    rt_t = rotation_transpose_vec(rotation, image.tvec)
    return (-rt_t[0], -rt_t[1], -rt_t[2])


def world_to_viewer(point: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = point
    return (x, z, -y)


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


def read_camera(path: Path) -> dict[int, Camera]:
    cameras: dict[int, Camera] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
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


def read_images(path: Path) -> list[Image]:
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
    images = []
    for index in range(0, len(lines), 2):
        tokens = lines[index].split()
        images.append(
            Image(
                image_id=int(tokens[0]),
                qvec=tuple(map(float, tokens[1:5])),
                tvec=tuple(map(float, tokens[5:8])),
                camera_id=int(tokens[8]),
                name=" ".join(tokens[9:]),
            )
        )
    return images


def read_points(path: Path, max_points: int) -> list[Point3D]:
    points = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        tokens = line.split()
        track_tokens = tokens[8:]
        points.append(
            Point3D(
                point3d_id=int(tokens[0]),
                xyz=tuple(map(float, tokens[1:4])),
                rgb=tuple(map(int, tokens[4:7])),
                track_len=len(track_tokens) // 2,
            )
        )
    if len(points) <= max_points:
        return points
    step = len(points) / max_points
    return [points[int(index * step)] for index in range(max_points)]


def read_gcps(input_dir: Path) -> list[Gcp]:
    gcp_path = input_dir / "gcp.txt"
    obs_path = input_dir / "gcp_observations.txt"
    if not gcp_path.exists() or not obs_path.exists():
        return []
    observation_counts = {}
    for line in obs_path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        tokens = line.split()
        observation_counts[int(tokens[0])] = int(tokens[1])
    gcps = []
    for line in gcp_path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        tokens = line.split()
        gcp_id = int(tokens[0])
        observation_count = observation_counts.get(gcp_id, 0)
        if observation_count <= 0:
            continue
        gcps.append(
            Gcp(
                gcp_id=gcp_id,
                name=tokens[1],
                xyz=tuple(map(float, tokens[2:5])),
                is_check_point=tokens[7] == "1",
                observation_count=observation_count,
            )
        )
    return gcps


def read_noise_metadata(input_dir: Path) -> dict:
    path = input_dir / "noise_metadata.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def detect_format(input_dir: Path, requested_format: str) -> str:
    if requested_format != "auto":
        return requested_format
    if (input_dir / "cameras.txt").exists() and (input_dir / "images.txt").exists() and (input_dir / "points3D.txt").exists():
        return "colmap"
    if (input_dir / "cal.txt").exists() and (input_dir / "XYZ.txt").exists() and (input_dir / "Feature.txt").exists():
        return "pvl-ba"
    raise FileNotFoundError("Could not detect COLMAP or PVL-BA dataset files")


def read_pvl_intrinsics(path: Path) -> list[tuple[float, float, float, float]]:
    rows = [list(map(float, line.split())) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    intrinsics = []
    for index in range(0, len(rows), 3):
        K = rows[index : index + 3]
        intrinsics.append((K[0][0], K[1][1], K[0][2], K[1][2]))
    return intrinsics


def find_pvl_cam_file(input_dir: Path) -> Path:
    matches = sorted(input_dir.glob("Cam*-*.txt"))
    if not matches:
        matches = sorted(input_dir.glob("Cam.txt"))
    if not matches:
        raise FileNotFoundError("No PVL-BA camera file found")
    return matches[0]


def read_pvl_cameras(input_dir: Path) -> list[PvlCamera]:
    intrinsics = read_pvl_intrinsics(input_dir / "cal.txt")
    cameras = []
    for line in find_pvl_cam_file(input_dir).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        values = list(map(float, line.split()))
        group_index = int(values[6]) - 1
        fx, fy, cx, cy = intrinsics[group_index]
        cameras.append(
            PvlCamera(
                rotation=rotation_from_euler(values[0], values[1], values[2]),
                center=tuple(values[3:6]),
                intrinsics=(fx, fy, cx, cy),
                width=max(1, int(round(cx * 2.0))),
                height=max(1, int(round(cy * 2.0))),
            )
        )
    return cameras


def read_pvl_track_lengths(path: Path) -> list[int]:
    return [int(line.split()[0]) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_pvl_points(input_dir: Path, max_points: int) -> list[Point3D]:
    track_lengths = read_pvl_track_lengths(input_dir / "Feature.txt")
    points = []
    for index, line in enumerate((input_dir / "XYZ.txt").read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        points.append(Point3D(index, tuple(map(float, line.split())), (210, 220, 190), track_lengths[index - 1]))
    if len(points) <= max_points:
        return points
    step = len(points) / max_points
    return [points[int(index * step)] for index in range(max_points)]


def pvl_project(point: tuple[float, float, float], camera: PvlCamera) -> tuple[float, float]:
    vector = tuple(point[index] - camera.center[index] for index in range(3))
    x_cam, y_cam, z_cam = mat_vec(camera.rotation, vector)
    fx, fy, cx, cy = camera.intrinsics
    return fx * x_cam / z_cam + cx, fy * y_cam / z_cam + cy


def pvl_rmse(input_dir: Path, cameras: list[PvlCamera]) -> tuple[int, float]:
    points = [tuple(map(float, line.split())) for line in (input_dir / "XYZ.txt").read_text(encoding="utf-8").splitlines() if line.strip()]
    sum_squared = 0.0
    count = 0
    for point, line in zip(points, (input_dir / "Feature.txt").read_text(encoding="utf-8").splitlines()):
        tokens = line.split()
        track_len = int(tokens[0])
        for observation_index in range(track_len):
            image_index = int(tokens[1 + 3 * observation_index])
            observed_u = float(tokens[2 + 3 * observation_index])
            observed_v = float(tokens[3 + 3 * observation_index])
            projected_u, projected_v = pvl_project(point, cameras[image_index])
            sum_squared += (projected_u - observed_u) ** 2 + (projected_v - observed_v) ** 2
            count += 1
    return count, math.sqrt(sum_squared / count)


def bounds(points: list[tuple[float, float, float]]) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    mins = [min(point[index] for point in points) for index in range(3)]
    maxs = [max(point[index] for point in points) for index in range(3)]
    return tuple(mins), tuple(maxs)  # type: ignore[return-value]


def diagonal_from_bounds(min_bound: tuple[float, float, float], max_bound: tuple[float, float, float]) -> float:
    return math.sqrt(sum((max_bound[index] - min_bound[index]) ** 2 for index in range(3)))


def bounds_payload(points: list[tuple[float, float, float]]) -> dict:
    min_bound, max_bound = bounds(points)
    center = tuple((min_bound[index] + max_bound[index]) * 0.5 for index in range(3))
    return {"min": min_bound, "max": max_bound, "center": center, "diagonal": diagonal_from_bounds(min_bound, max_bound)}


def quantile(sorted_values: list[float], ratio: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = max(0.0, min(1.0, ratio)) * (len(sorted_values) - 1)
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return sorted_values[low]
    fraction = position - low
    return sorted_values[low] * (1.0 - fraction) + sorted_values[high] * fraction


def robust_bounds_payload(points: list[tuple[float, float, float]]) -> dict:
    if len(points) < 100:
        return bounds_payload(points)
    axis_values = [[point[index] for point in points] for index in range(3)]
    limits = []
    for values in axis_values:
        values.sort()
        limits.append((quantile(values, 0.01), quantile(values, 0.99)))
    filtered = [
        point
        for point in points
        if all(limits[index][0] <= point[index] <= limits[index][1] for index in range(3))
    ]
    if len(filtered) < max(20, len(points) // 2):
        return bounds_payload(points)
    return bounds_payload(filtered)


def frustum_segments(image: Image, camera: Camera, scale: float) -> list[tuple[float, float, float]]:
    rotation = qvec_to_rotation(image.qvec)
    center = camera_center(image)
    fx = camera.params[0]
    fy = camera.params[1]
    cx = camera.params[2]
    cy = camera.params[3]
    z = scale
    corners_camera = [
        ((0.0 - cx) / fx * z, (0.0 - cy) / fy * z, z),
        ((camera.width - cx) / fx * z, (0.0 - cy) / fy * z, z),
        ((camera.width - cx) / fx * z, (camera.height - cy) / fy * z, z),
        ((0.0 - cx) / fx * z, (camera.height - cy) / fy * z, z),
    ]
    corners_world = []
    for corner in corners_camera:
        offset = rotation_transpose_vec(rotation, corner)
        corners_world.append(tuple(center[index] + offset[index] for index in range(3)))
    pairs = [
        (center, corners_world[0]),
        (center, corners_world[1]),
        (center, corners_world[2]),
        (center, corners_world[3]),
        (corners_world[0], corners_world[1]),
        (corners_world[1], corners_world[2]),
        (corners_world[2], corners_world[3]),
        (corners_world[3], corners_world[0]),
    ]
    vertices = []
    for start, end in pairs:
        vertices.append(world_to_viewer(start))
        vertices.append(world_to_viewer(end))
    return vertices


def pvl_frustum_segments(camera: PvlCamera, scale: float) -> list[tuple[float, float, float]]:
    fx, fy, cx, cy = camera.intrinsics
    z = scale
    corners_camera = [
        ((0.0 - cx) / fx * z, (0.0 - cy) / fy * z, z),
        ((camera.width - cx) / fx * z, (0.0 - cy) / fy * z, z),
        ((camera.width - cx) / fx * z, (camera.height - cy) / fy * z, z),
        ((0.0 - cx) / fx * z, (camera.height - cy) / fy * z, z),
    ]
    corners_world = []
    for corner in corners_camera:
        offset = rotation_transpose_vec(camera.rotation, corner)
        corners_world.append(tuple(camera.center[index] + offset[index] for index in range(3)))
    pairs = [
        (camera.center, corners_world[0]),
        (camera.center, corners_world[1]),
        (camera.center, corners_world[2]),
        (camera.center, corners_world[3]),
        (corners_world[0], corners_world[1]),
        (corners_world[1], corners_world[2]),
        (corners_world[2], corners_world[3]),
        (corners_world[3], corners_world[0]),
    ]
    vertices = []
    for start, end in pairs:
        vertices.append(world_to_viewer(start))
        vertices.append(world_to_viewer(end))
    return vertices


def build_colmap_payload(input_dir: Path, max_points: int, camera_stride: int) -> dict:
    cameras = read_camera(input_dir / "cameras.txt")
    images = read_images(input_dir / "images.txt")
    points = read_points(input_dir / "points3D.txt", max_points)
    gcps = read_gcps(input_dir)
    centers = [camera_center(image) for image in images]
    all_xyz = [point.xyz for point in points] + centers + [gcp.xyz for gcp in gcps]
    min_bound, max_bound = bounds(all_xyz)
    diagonal = math.sqrt(sum((max_bound[index] - min_bound[index]) ** 2 for index in range(3)))
    viewer_points = [world_to_viewer(point.xyz) for point in points]
    viewer_centers = [world_to_viewer(center) for center in centers]
    viewer_gcps = [world_to_viewer(gcp.xyz) for gcp in gcps]
    viewer_positions = viewer_points + viewer_centers + viewer_gcps
    viewer_bounds = bounds_payload(viewer_positions)
    view_bounds = robust_bounds_payload(viewer_positions)
    frustum_scale = max(view_bounds["diagonal"] * 0.025, 1.0)

    sampled_images = images[:: max(1, camera_stride)]
    frustum_vertices = []
    for image in sampled_images:
        frustum_vertices.extend(frustum_segments(image, cameras[image.camera_id], frustum_scale))

    return {
        "format": "COLMAP",
        "source": str(input_dir),
        "stats": {
            "points": len(points),
            "images": len(images),
            "frustums": len(sampled_images),
            "gcps": len(gcps),
            "maxPoints": max_points,
        },
        "bounds": viewer_bounds,
        "viewBounds": view_bounds,
        "points": {
            "positions": viewer_points,
            "colors": [[channel / 255.0 for channel in point.rgb] for point in points],
            "trackLengths": [point.track_len for point in points],
        },
        "cameras": {
            "centers": viewer_centers,
            "names": [image.name for image in images],
            "frustumVertices": frustum_vertices,
        },
        "gcps": [
            {
                "id": gcp.gcp_id,
                "name": gcp.name,
                "position": viewer_gcps[index],
                "world": gcp.xyz,
                "isCheckPoint": gcp.is_check_point,
                "observations": gcp.observation_count,
            }
            for index, gcp in enumerate(gcps)
        ],
    }


def build_pvl_payload(input_dir: Path, max_points: int, camera_stride: int) -> dict:
    cameras = read_pvl_cameras(input_dir)
    points = read_pvl_points(input_dir, max_points)
    gcps = read_gcps(input_dir)
    centers = [camera.center for camera in cameras]
    all_xyz = [point.xyz for point in points] + centers + [gcp.xyz for gcp in gcps]
    min_bound, max_bound = bounds(all_xyz)
    diagonal = math.sqrt(sum((max_bound[index] - min_bound[index]) ** 2 for index in range(3)))
    viewer_points = [world_to_viewer(point.xyz) for point in points]
    viewer_centers = [world_to_viewer(center) for center in centers]
    viewer_gcps = [world_to_viewer(gcp.xyz) for gcp in gcps]
    viewer_positions = viewer_points + viewer_centers + viewer_gcps
    viewer_bounds = bounds_payload(viewer_positions)
    view_bounds = robust_bounds_payload(viewer_positions)
    frustum_scale = max(view_bounds["diagonal"] * 0.025, 1.0)
    sampled_cameras = cameras[:: max(1, camera_stride)]
    frustum_vertices = []
    for camera in sampled_cameras:
        frustum_vertices.extend(pvl_frustum_segments(camera, frustum_scale))
    observation_count, rmse = pvl_rmse(input_dir, cameras)
    noise_metadata = read_noise_metadata(input_dir)
    stats = {
        "points": len(points),
        "images": len(cameras),
        "frustums": len(sampled_cameras),
        "gcps": len(gcps),
        "maxPoints": max_points,
        "observations": observation_count,
        "rmsePx": rmse,
    }
    if noise_metadata:
        stats["targetRmsePx"] = noise_metadata.get("target_rmse_px")
        stats["noiseScalePx"] = noise_metadata.get("noise_scale_px")
    return {
        "format": "PVL-BA",
        "source": str(input_dir),
        "stats": stats,
        "noiseMetadata": noise_metadata,
        "bounds": viewer_bounds,
        "viewBounds": view_bounds,
        "points": {
            "positions": viewer_points,
            "colors": [[channel / 255.0 for channel in point.rgb] for point in points],
            "trackLengths": [point.track_len for point in points],
        },
        "cameras": {
            "centers": viewer_centers,
            "names": [f"camera_{index:06d}" for index in range(len(cameras))],
            "frustumVertices": frustum_vertices,
        },
        "gcps": [
            {
                "id": gcp.gcp_id,
                "name": gcp.name,
                "position": viewer_gcps[index],
                "world": gcp.xyz,
                "isCheckPoint": gcp.is_check_point,
                "observations": gcp.observation_count,
            }
            for index, gcp in enumerate(gcps)
        ],
    }


def build_payload(input_dir: Path, requested_format: str, max_points: int, camera_stride: int) -> dict:
    dataset_format = detect_format(input_dir, requested_format)
    if dataset_format == "colmap":
        return build_colmap_payload(input_dir, max_points, camera_stride)
    return build_pvl_payload(input_dir, max_points, camera_stride)


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>BA Dataset Viewer</title>
  <style>
    html, body { margin: 0; width: 100%; height: 100%; overflow: hidden; font-family: Inter, Segoe UI, Arial, sans-serif; background: #101215; color: #eef2f6; }
    #scene { position: fixed; inset: 0; }
    #panel { position: fixed; left: 16px; top: 16px; width: min(360px, calc(100vw - 32px)); max-height: calc(100vh - 32px); overflow: auto; background: rgba(18, 22, 27, 0.88); border: 1px solid rgba(255,255,255,0.12); border-radius: 8px; box-shadow: 0 18px 48px rgba(0,0,0,0.35); backdrop-filter: blur(10px); }
    #panel header { padding: 14px 16px 10px; border-bottom: 1px solid rgba(255,255,255,0.10); }
    h1 { font-size: 16px; margin: 0 0 8px; font-weight: 650; letter-spacing: 0; }
    .source { color: #aeb8c3; font-size: 12px; line-height: 1.35; overflow-wrap: anywhere; }
    .section { padding: 12px 16px; border-bottom: 1px solid rgba(255,255,255,0.08); }
    .stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
    .stat { background: rgba(255,255,255,0.06); border-radius: 6px; padding: 8px; }
    .stat span { display: block; color: #aeb8c3; font-size: 11px; }
    .stat strong { display: block; margin-top: 2px; font-size: 16px; }
    label { display: flex; align-items: center; justify-content: space-between; gap: 12px; font-size: 13px; color: #dce3ea; margin: 8px 0; }
    input[type="range"] { width: 145px; accent-color: #52c7b8; }
    input[type="checkbox"] { accent-color: #52c7b8; }
    button { width: 32px; height: 32px; border: 1px solid rgba(255,255,255,0.14); border-radius: 6px; background: rgba(255,255,255,0.08); color: #eef2f6; cursor: pointer; font-size: 16px; }
    button:hover { background: rgba(255,255,255,0.14); }
    .row { display: flex; align-items: center; gap: 8px; }
    .gcp { display: grid; grid-template-columns: 44px 1fr auto; gap: 8px; align-items: center; font-size: 12px; padding: 6px 0; border-bottom: 1px solid rgba(255,255,255,0.06); }
    .gcp:last-child { border-bottom: 0; }
    .badge { color: #101215; background: #ffcc66; border-radius: 999px; padding: 2px 7px; font-weight: 700; text-align: center; }
    .empty { color: #aeb8c3; font-size: 12px; }
    #hint { position: fixed; right: 16px; bottom: 14px; color: rgba(238,242,246,0.78); font-size: 12px; background: rgba(18, 22, 27, 0.68); border-radius: 6px; padding: 8px 10px; }
  </style>
</head>
<body>
  <canvas id="scene"></canvas>
  <aside id="panel">
    <header>
      <h1>BA Dataset Viewer</h1>
      <div class="source" id="source"></div>
    </header>
    <section class="section stats" id="stats"></section>
    <section class="section">
      <label><span>Points</span><input id="togglePoints" type="checkbox" checked></label>
      <label><span>Cameras</span><input id="toggleCameras" type="checkbox" checked></label>
      <label><span>GCPs</span><input id="toggleGcps" type="checkbox" checked></label>
      <label><span>Point size</span><input id="pointSize" type="range" min="0.01" max="0.35" value="0.08" step="0.01"></label>
      <label><span>Camera opacity</span><input id="cameraOpacity" type="range" min="0.05" max="1" value="0.5" step="0.05"></label>
      <label><span>Frustum size</span><input id="frustumScale" type="range" min="0.1" max="5" value="0.1" step="0.05"></label>
      <div class="row"><button id="resetView" title="Reset view">H</button><button id="topView" title="Top view">T</button></div>
    </section>
    <section class="section">
      <h1>Ground Control Points</h1>
      <div id="gcps"></div>
    </section>
  </aside>
  <div id="hint">Drag to rotate - wheel to zoom - right-drag to pan</div>
  <script id="payload" type="application/json">__PAYLOAD__</script>
  <script type="importmap">
    {
      "imports": {
        "three": "https://unpkg.com/three@0.165.0/build/three.module.js",
        "three/addons/": "https://unpkg.com/three@0.165.0/examples/jsm/"
      }
    }
  </script>
  <script type="module">
    import * as THREE from 'three';
    import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

    const data = JSON.parse(document.getElementById('payload').textContent);
    document.getElementById('source').textContent = `${data.format} - ${data.source}`;

    const stats = document.getElementById('stats');
    const statRows = [['Images', data.stats.images], ['Frustums', data.stats.frustums], ['Points', data.stats.points], ['GCPs', data.stats.gcps]];
    if (data.stats.observations !== undefined) statRows.push(['Obs.', data.stats.observations]);
    if (data.stats.rmsePx !== undefined) statRows.push(['RMSE px', Number(data.stats.rmsePx).toFixed(2)]);
    if (data.stats.targetRmsePx !== undefined) statRows.push(['Target px', Number(data.stats.targetRmsePx).toFixed(2)]);
    for (const [label, value] of statRows) {
      const node = document.createElement('div');
      node.className = 'stat';
      node.innerHTML = `<span>${label}</span><strong>${typeof value === 'number' ? value.toLocaleString() : value}</strong>`;
      stats.appendChild(node);
    }

    const canvas = document.getElementById('scene');
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.setClearColor(0x101215, 1);

    const scene = new THREE.Scene();
    const sceneDiagonal = Math.max(data.bounds.diagonal, 1);
    const frameBounds = data.viewBounds || data.bounds;
    const camera = new THREE.PerspectiveCamera(55, window.innerWidth / window.innerHeight, 0.01, Math.max(sceneDiagonal * 20, 1000));
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;

    const center = new THREE.Vector3(...frameBounds.center);
    const diagonal = Math.max(frameBounds.diagonal, 1);
    function resetView(top=false) {
      controls.target.copy(center);
      if (top) {
        camera.position.set(center.x, center.y + diagonal * 1.35, center.z + 0.001);
      } else {
        camera.position.set(center.x - diagonal * 0.65, center.y + diagonal * 0.45, center.z + diagonal * 0.75);
      }
      camera.near = Math.max(diagonal / 100000, 0.001);
      camera.far = Math.max(sceneDiagonal * 20, diagonal * 20, 1000);
      camera.updateProjectionMatrix();
      controls.update();
    }

    const grid = new THREE.GridHelper(diagonal * 1.25, 12, 0x3b4752, 0x252d35);
    grid.position.copy(center);
    scene.add(grid);

    const axes = new THREE.AxesHelper(diagonal * 0.12);
    axes.position.copy(center);
    scene.add(axes);

    const pointGeometry = new THREE.BufferGeometry();
    pointGeometry.setAttribute('position', new THREE.Float32BufferAttribute(data.points.positions.flat(), 3));
    pointGeometry.setAttribute('color', new THREE.Float32BufferAttribute(data.points.colors.flat(), 3));
    const pointMaterial = new THREE.PointsMaterial({ size: 0.08, vertexColors: true, sizeAttenuation: true });
    const pointCloud = new THREE.Points(pointGeometry, pointMaterial);
    scene.add(pointCloud);

    const cameraCenterGeometry = new THREE.BufferGeometry();
    cameraCenterGeometry.setAttribute('position', new THREE.Float32BufferAttribute(data.cameras.centers.flat(), 3));
    const cameraCenterMaterial = new THREE.PointsMaterial({ size: 0.45, color: 0x52c7b8, sizeAttenuation: true });
    const cameraCenters = new THREE.Points(cameraCenterGeometry, cameraCenterMaterial);
    scene.add(cameraCenters);

    const baseFrustumPositions = new Float32Array(data.cameras.frustumVertices.flat());
    const frustumGeometry = new THREE.BufferGeometry();
    frustumGeometry.setAttribute('position', new THREE.BufferAttribute(baseFrustumPositions.slice(), 3));
    const frustumMaterial = new THREE.LineBasicMaterial({ color: 0x52c7b8, transparent: true, opacity: 0.5 });
    const frustums = new THREE.LineSegments(frustumGeometry, frustumMaterial);
    scene.add(frustums);

    function setFrustumScale(scale) {
      const attribute = frustumGeometry.getAttribute('position');
      const positions = attribute.array;
      const floatsPerFrustum = 16 * 3;
      for (let offset = 0; offset < baseFrustumPositions.length; offset += floatsPerFrustum) {
        const centerX = baseFrustumPositions[offset];
        const centerY = baseFrustumPositions[offset + 1];
        const centerZ = baseFrustumPositions[offset + 2];
        const end = Math.min(offset + floatsPerFrustum, baseFrustumPositions.length);
        for (let index = offset; index < end; index += 3) {
          positions[index] = centerX + (baseFrustumPositions[index] - centerX) * scale;
          positions[index + 1] = centerY + (baseFrustumPositions[index + 1] - centerY) * scale;
          positions[index + 2] = centerZ + (baseFrustumPositions[index + 2] - centerZ) * scale;
        }
      }
      attribute.needsUpdate = true;
      frustumGeometry.computeBoundingSphere();
    }

    const gcpGroup = new THREE.Group();
    const gcpGeometry = new THREE.BufferGeometry();
    gcpGeometry.setAttribute('position', new THREE.Float32BufferAttribute(data.gcps.map(g => g.position).flat(), 3));
    const gcpMaterial = new THREE.PointsMaterial({ size: 1.3, color: 0xffcc66, sizeAttenuation: true });
    gcpGroup.add(new THREE.Points(gcpGeometry, gcpMaterial));
    scene.add(gcpGroup);

    const gcpList = document.getElementById('gcps');
    if (data.gcps.length === 0) {
      gcpList.textContent = 'No observed GCPs found.';
      gcpList.className = 'empty';
    } else {
      for (const gcp of data.gcps) {
        const node = document.createElement('div');
        node.className = 'gcp';
        node.innerHTML = `<span class="badge">${gcp.name}</span><span>${gcp.observations} observations</span><span>${gcp.isCheckPoint ? 'check' : 'control'}</span>`;
        gcpList.appendChild(node);
      }
    }

    document.getElementById('togglePoints').addEventListener('change', event => pointCloud.visible = event.target.checked);
    document.getElementById('toggleCameras').addEventListener('change', event => {
      cameraCenters.visible = event.target.checked;
      frustums.visible = event.target.checked;
    });
    document.getElementById('toggleGcps').addEventListener('change', event => gcpGroup.visible = event.target.checked);
    document.getElementById('pointSize').addEventListener('input', event => pointMaterial.size = Number(event.target.value));
    document.getElementById('cameraOpacity').addEventListener('input', event => frustumMaterial.opacity = Number(event.target.value));
    document.getElementById('frustumScale').addEventListener('input', event => setFrustumScale(Number(event.target.value)));
    document.getElementById('resetView').addEventListener('click', () => resetView(false));
    document.getElementById('topView').addEventListener('click', () => resetView(true));

    window.addEventListener('resize', () => {
      camera.aspect = window.innerWidth / window.innerHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(window.innerWidth, window.innerHeight);
    });

    setFrustumScale(0.1);
    resetView(false);
    renderer.setAnimationLoop(() => {
      controls.update();
      renderer.render(scene, camera);
    });
  </script>
</body>
</html>
"""


def main() -> None:
    args = parse_args()
    payload = build_payload(args.input_dir.resolve(), args.format, args.max_points, args.camera_stride)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    html = HTML_TEMPLATE.replace("__PAYLOAD__", json.dumps(payload, separators=(",", ":")))
    args.output.write_text(html, encoding="utf-8", newline="\n")
    print(f"Wrote BA viewer to {args.output.resolve()}")
    print(f"  images: {payload['stats']['images']}")
    print(f"  camera frustums: {payload['stats']['frustums']}")
    print(f"  points: {payload['stats']['points']}")
    print(f"  gcps: {payload['stats']['gcps']}")


if __name__ == "__main__":
    main()

