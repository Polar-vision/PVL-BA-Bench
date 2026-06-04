#!/usr/bin/env python3
"""Generate a linked quality-level viewer for PVL-BA benchmark variants."""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import generate_viewer


QUALITY_RE = re.compile(r"(init|obs)-rmse(\d+)p(\d+)px", re.IGNORECASE)


@dataclass(frozen=True)
class QualityDataset:
    path: Path
    name: str
    label: str
    group: str
    sort_key: tuple[int, float, str]
    target_rmse_px: float | None
    mode_tag: str
    is_reference: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", required=True, type=Path, help="Root directory containing PVL-BA quality variants")
    parser.add_argument("--reference-dir", type=Path, help="Optional PVL-BA original/reference dataset directory")
    parser.add_argument("--output", required=True, type=Path, help="Output HTML file")
    parser.add_argument("--max-points", type=int, default=100_000, help="Maximum sparse points per quality level")
    parser.add_argument("--camera-stride", type=int, default=1, help="Embed every Nth camera frustum")
    parser.add_argument(
        "--stress-threshold",
        type=float,
        default=200.0,
        help="RMSE values greater than or equal to this threshold are grouped as stress tests",
    )
    return parser.parse_args()


def is_pvl_ba_dir(path: Path) -> bool:
    return (path / "cal.txt").exists() and (path / "XYZ.txt").exists() and (path / "Feature.txt").exists()


def rmse_from_name(name: str) -> tuple[str, float] | None:
    match = QUALITY_RE.search(name)
    if not match:
        return None
    integer_part = int(match.group(2))
    decimal_part = int(match.group(3)) / (10 ** len(match.group(3)))
    return match.group(1).lower(), integer_part + decimal_part


def discover_datasets(input_root: Path, stress_threshold: float, reference_dir: Path | None = None) -> list[QualityDataset]:
    datasets: list[QualityDataset] = []
    if reference_dir is not None:
        if not is_pvl_ba_dir(reference_dir):
            raise FileNotFoundError(f"Reference directory is not a PVL-BA dataset: {reference_dir}")
        datasets.append(
            QualityDataset(
                path=reference_dir,
                name=reference_dir.name,
                label="original",
                group="Reference",
                sort_key=(0, -1.0, reference_dir.name),
                target_rmse_px=None,
                mode_tag="reference",
                is_reference=True,
            )
        )
    for path in sorted(input_root.iterdir()):
        if not path.is_dir() or not is_pvl_ba_dir(path):
            continue
        name = path.name
        parsed = rmse_from_name(name)
        if parsed:
            mode_tag, target_rmse_px = parsed
            label_prefix = "init" if mode_tag == "init" else "obs"
            label = f"{label_prefix} {target_rmse_px:g} px"
            group = "Stress Test" if target_rmse_px >= stress_threshold else "Main Benchmark"
            sort_group = 2 if group == "Stress Test" else 1
            datasets.append(
                QualityDataset(
                    path=path,
                    name=name,
                    label=label,
                    group=group,
                    sort_key=(sort_group, target_rmse_px, name),
                    target_rmse_px=target_rmse_px,
                    mode_tag=mode_tag,
                    is_reference=False,
                )
            )
        elif reference_dir is None and ("original" in name.lower() or "reference" in name.lower()):
            datasets.append(
                QualityDataset(
                    path=path,
                    name=name,
                    label="original",
                    group="Reference",
                    sort_key=(0, -1.0, name),
                    target_rmse_px=None,
                    mode_tag="reference",
                    is_reference=True,
                )
            )
    datasets.sort(key=lambda item: item.sort_key)
    if not datasets:
        raise FileNotFoundError(f"No PVL-BA datasets found below {input_root}")
    return datasets


def percentile(sorted_values: list[float], ratio: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, max(0, int(ratio * len(sorted_values))))
    return sorted_values[index]


def error_summary(errors: list[float], sum_squared: float) -> dict:
    if not errors:
        return {
            "count": 0,
            "meanPx": 0.0,
            "rmsePx": 0.0,
            "medianPx": 0.0,
            "p90Px": 0.0,
            "p95Px": 0.0,
            "p99Px": 0.0,
            "maxPx": 0.0,
        }
    errors.sort()
    count = len(errors)
    return {
        "count": count,
        "meanPx": sum(errors) / count,
        "rmsePx": math.sqrt(sum_squared / count),
        "medianPx": errors[count // 2],
        "p90Px": percentile(errors, 0.90),
        "p95Px": percentile(errors, 0.95),
        "p99Px": percentile(errors, 0.99),
        "maxPx": errors[-1],
    }


def read_pvl_points(input_dir: Path) -> list[tuple[float, float, float]]:
    return [tuple(map(float, line.split())) for line in (input_dir / "XYZ.txt").read_text(encoding="utf-8").splitlines() if line.strip()]


def compute_tie_metrics(input_dir: Path, cameras: list[generate_viewer.PvlCamera]) -> dict:
    points = read_pvl_points(input_dir)
    errors: list[float] = []
    sum_squared = 0.0
    depth_min = float("inf")
    depth_max = -float("inf")
    negative_depth_count = 0
    with (input_dir / "Feature.txt").open(encoding="utf-8") as feature_file:
        for point, line in zip(points, feature_file):
            if not line.strip():
                continue
            tokens = line.split()
            track_len = int(tokens[0])
            for observation_index in range(track_len):
                image_index = int(tokens[1 + 3 * observation_index])
                observed_u = float(tokens[2 + 3 * observation_index])
                observed_v = float(tokens[3 + 3 * observation_index])
                camera = cameras[image_index]
                vector = tuple(point[index] - camera.center[index] for index in range(3))
                x_cam, y_cam, z_cam = generate_viewer.mat_vec(camera.rotation, vector)
                depth_min = min(depth_min, z_cam)
                depth_max = max(depth_max, z_cam)
                if z_cam < 0.0:
                    negative_depth_count += 1
                fx, fy, cx, cy = camera.intrinsics
                projected_u = fx * x_cam / z_cam + cx
                projected_v = fy * y_cam / z_cam + cy
                error = math.hypot(projected_u - observed_u, projected_v - observed_v)
                errors.append(error)
                sum_squared += error * error
    summary = error_summary(errors, sum_squared)
    summary["negativeDepthCount"] = negative_depth_count
    summary["depthMin"] = depth_min if errors else 0.0
    summary["depthMax"] = depth_max if errors else 0.0
    return summary


def read_gcp_points(input_dir: Path) -> dict[int, tuple[float, float, float]]:
    gcp_path = input_dir / "gcp.txt"
    if not gcp_path.exists():
        return {}
    gcps = {}
    for line in gcp_path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        tokens = line.split()
        gcps[int(tokens[0])] = tuple(map(float, tokens[2:5]))
    return gcps


def compute_gcp_metrics(input_dir: Path, cameras: list[generate_viewer.PvlCamera]) -> dict:
    gcp_obs_path = input_dir / "gcp_observations.txt"
    gcps = read_gcp_points(input_dir)
    if not gcps or not gcp_obs_path.exists():
        return {}
    errors: list[float] = []
    sum_squared = 0.0
    for line in gcp_obs_path.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        tokens = line.split()
        gcp_id = int(tokens[0])
        if gcp_id not in gcps:
            continue
        point = gcps[gcp_id]
        track_len = int(tokens[1])
        for observation_index in range(track_len):
            image_index = int(tokens[2 + 3 * observation_index])
            observed_u = float(tokens[3 + 3 * observation_index])
            observed_v = float(tokens[4 + 3 * observation_index])
            projected_u, projected_v = generate_viewer.pvl_project(point, cameras[image_index])
            error = math.hypot(projected_u - observed_u, projected_v - observed_v)
            errors.append(error)
            sum_squared += error * error
    return error_summary(errors, sum_squared)


def enrich_payload(payload: dict, dataset: QualityDataset, input_dir: Path) -> dict:
    cameras = generate_viewer.read_pvl_cameras(input_dir)
    tie_metrics = compute_tie_metrics(input_dir, cameras)
    gcp_metrics = compute_gcp_metrics(input_dir, cameras)
    metadata = payload.get("noiseMetadata") or {}
    stats = payload["stats"]
    stats.update(
        {
            "observations": tie_metrics["count"],
            "rmsePx": tie_metrics["rmsePx"],
            "meanPx": tie_metrics["meanPx"],
            "medianPx": tie_metrics["medianPx"],
            "p90Px": tie_metrics["p90Px"],
            "p95Px": tie_metrics["p95Px"],
            "p99Px": tie_metrics["p99Px"],
            "maxPx": tie_metrics["maxPx"],
            "negativeDepthCount": tie_metrics["negativeDepthCount"],
            "depthMin": tie_metrics["depthMin"],
            "depthMax": tie_metrics["depthMax"],
            "gcpObservations": gcp_metrics.get("count", 0),
            "gcpRmsePx": gcp_metrics.get("rmsePx"),
            "gcpP95Px": gcp_metrics.get("p95Px"),
            "poseScale": metadata.get("pose_noise_scale"),
        }
    )
    if dataset.target_rmse_px is not None:
        stats["targetRmsePx"] = dataset.target_rmse_px
    return {
        "name": dataset.name,
        "label": dataset.label,
        "group": dataset.group,
        "modeTag": dataset.mode_tag,
        "isReference": dataset.is_reference,
        "targetRmsePx": dataset.target_rmse_px,
        "source": str(input_dir),
        "stats": stats,
        "bounds": payload["bounds"],
        "points": payload["points"],
        "cameras": payload["cameras"],
        "gcps": payload["gcps"],
        "noiseMetadata": metadata,
    }


def global_bounds(datasets: list[dict]) -> dict:
    positions: list[tuple[float, float, float]] = []
    for dataset in datasets:
        positions.extend(tuple(point) for point in dataset["points"]["positions"])
        positions.extend(tuple(center) for center in dataset["cameras"]["centers"])
        positions.extend(tuple(gcp["position"]) for gcp in dataset["gcps"])
    min_bound, max_bound = generate_viewer.bounds(positions)
    center = tuple((min_bound[index] + max_bound[index]) * 0.5 for index in range(3))
    diagonal = math.sqrt(sum((max_bound[index] - min_bound[index]) ** 2 for index in range(3)))
    return {"min": min_bound, "max": max_bound, "center": center, "diagonal": diagonal}


def build_quality_payload(
    input_root: Path,
    max_points: int,
    camera_stride: int,
    stress_threshold: float,
    reference_dir: Path | None = None,
) -> dict:
    discovered = discover_datasets(input_root, stress_threshold, reference_dir)
    raw_items = []
    for dataset in discovered:
        payload = generate_viewer.build_pvl_payload(dataset.path.resolve(), max_points, camera_stride)
        raw_items.append((dataset, payload, generate_viewer.read_pvl_cameras(dataset.path.resolve())))
    datasets = [enrich_payload(payload, dataset, dataset.path.resolve()) for dataset, payload, _cameras in raw_items]
    shared_bounds = global_bounds(datasets)
    reference_index = next((index for index, dataset in enumerate(datasets) if dataset["isReference"]), 0)
    reference_diagonal = datasets[reference_index]["bounds"]["diagonal"]
    shared_diagonal = max(reference_diagonal, 1.0)
    shared_frustum_scale = max(shared_diagonal * 0.025, 1.0)
    for output_dataset, (_dataset, _payload, cameras) in zip(datasets, raw_items):
        sampled_cameras = cameras[:: max(1, camera_stride)]
        frustum_vertices = []
        for camera in sampled_cameras:
            frustum_vertices.extend(generate_viewer.pvl_frustum_segments(camera, shared_frustum_scale))
        output_dataset["cameras"]["frustumVertices"] = frustum_vertices
        output_dataset["stats"]["sharedFrustumScale"] = shared_frustum_scale
    return {
        "format": "PVL-BA quality set",
        "source": str(input_root.resolve()),
        "bounds": shared_bounds,
        "groups": ["Reference", "Main Benchmark", "Stress Test"],
        "datasets": datasets,
        "referenceIndex": reference_index,
    }


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PVL-BA Quality Viewer</title>
  <style>
    html, body { margin: 0; width: 100%; height: 100%; overflow: hidden; font-family: Inter, Segoe UI, Arial, sans-serif; background: #101215; color: #eef2f6; }
    #scene { position: fixed; inset: 0; }
    #panel { position: fixed; left: 16px; top: 16px; width: min(410px, calc(100vw - 32px)); max-height: calc(100vh - 32px); overflow: auto; background: rgba(18, 22, 27, 0.90); border: 1px solid rgba(255,255,255,0.12); border-radius: 8px; box-shadow: 0 18px 48px rgba(0,0,0,0.35); backdrop-filter: blur(10px); }
    #panel header { padding: 14px 16px 10px; border-bottom: 1px solid rgba(255,255,255,0.10); }
    h1 { font-size: 16px; margin: 0 0 8px; font-weight: 650; letter-spacing: 0; }
    h2 { font-size: 12px; margin: 12px 0 8px; color: #aeb8c3; font-weight: 700; letter-spacing: 0.04em; text-transform: uppercase; }
    .source { color: #aeb8c3; font-size: 12px; line-height: 1.35; overflow-wrap: anywhere; }
    .section { padding: 12px 16px; border-bottom: 1px solid rgba(255,255,255,0.08); }
    .quality-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
    .quality-button { width: 100%; min-height: 34px; border: 1px solid rgba(255,255,255,0.14); border-radius: 6px; background: rgba(255,255,255,0.07); color: #eef2f6; cursor: pointer; font-size: 12px; }
    .quality-button:hover { background: rgba(255,255,255,0.13); }
    .quality-button.active { border-color: #52c7b8; background: rgba(82,199,184,0.24); color: #ffffff; }
    .quality-button.stress.active { border-color: #ffcc66; background: rgba(255,204,102,0.22); }
    .stats { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
    .stat { background: rgba(255,255,255,0.06); border-radius: 6px; padding: 8px; min-width: 0; }
    .stat span { display: block; color: #aeb8c3; font-size: 11px; }
    .stat strong { display: block; margin-top: 2px; font-size: 15px; overflow-wrap: anywhere; }
    label { display: flex; align-items: center; justify-content: space-between; gap: 12px; font-size: 13px; color: #dce3ea; margin: 8px 0; }
    input[type="range"] { width: 145px; accent-color: #52c7b8; }
    input[type="checkbox"] { accent-color: #52c7b8; }
    .view-button { width: 32px; height: 32px; border: 1px solid rgba(255,255,255,0.14); border-radius: 6px; background: rgba(255,255,255,0.08); color: #eef2f6; cursor: pointer; font-size: 14px; }
    .view-button:hover { background: rgba(255,255,255,0.14); }
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
      <h1>PVL-BA Quality Viewer</h1>
      <div class="source" id="source"></div>
    </header>
    <section class="section" id="qualityPanel"></section>
    <section class="section stats" id="stats"></section>
    <section class="section">
      <label><span>Points</span><input id="togglePoints" type="checkbox" checked></label>
      <label><span>Cameras</span><input id="toggleCameras" type="checkbox" checked></label>
      <label><span>GCPs</span><input id="toggleGcps" type="checkbox" checked></label>
      <label><span>Original overlay</span><input id="toggleReference" type="checkbox"></label>
      <label><span>Point size</span><input id="pointSize" type="range" min="0.01" max="0.35" value="0.08" step="0.01"></label>
      <label><span>Camera opacity</span><input id="cameraOpacity" type="range" min="0.05" max="1" value="0.8" step="0.05"></label>
      <label><span>Frustum size</span><input id="frustumScale" type="range" min="0.1" max="5" value="1" step="0.05"></label>
      <div class="row"><button class="view-button" id="resetView" title="Reset view">H</button><button class="view-button" id="topView" title="Top view">T</button></div>
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

    const payload = JSON.parse(document.getElementById('payload').textContent);
    const datasets = payload.datasets;
    const referenceIndex = payload.referenceIndex;
    let activeIndex = referenceIndex;
    let activeHandles = null;
    let referenceHandles = null;
    let activeGroup = null;
    let referenceGroup = null;

    const state = {
      points: true,
      cameras: true,
      gcps: true,
      reference: false,
      pointSize: 0.08,
      cameraOpacity: 0.8,
      frustumScale: 1.0,
    };

    document.getElementById('source').textContent = `${payload.format} - ${payload.source}`;

    const canvas = document.getElementById('scene');
    const renderer = new THREE.WebGLRenderer({ canvas, antialias: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    renderer.setSize(window.innerWidth, window.innerHeight);
    renderer.setClearColor(0x101215, 1);

    const scene = new THREE.Scene();
    const diagonal = Math.max(payload.bounds.diagonal, 1);
    const center = new THREE.Vector3(...payload.bounds.center);
    const camera = new THREE.PerspectiveCamera(55, window.innerWidth / window.innerHeight, 0.01, Math.max(diagonal * 20, 1000));
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;

    const grid = new THREE.GridHelper(diagonal * 1.25, 12, 0x3b4752, 0x252d35);
    grid.position.copy(center);
    scene.add(grid);

    const axes = new THREE.AxesHelper(diagonal * 0.12);
    axes.position.copy(center);
    scene.add(axes);

    function fmt(value, decimals=2) {
      if (value === null || value === undefined) return 'n/a';
      if (typeof value === 'number') return value.toLocaleString(undefined, { maximumFractionDigits: decimals, minimumFractionDigits: decimals });
      return value;
    }

    function makePointCloud(data, reference=false) {
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(data.points.positions.flat(), 3));
      const colors = reference
        ? data.points.positions.flatMap(() => [0.76, 0.78, 0.80])
        : data.points.colors.flat();
      geometry.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
      const material = new THREE.PointsMaterial({
        size: reference ? state.pointSize * 0.75 : state.pointSize,
        vertexColors: true,
        transparent: reference,
        opacity: reference ? 0.42 : 1.0,
        sizeAttenuation: true,
      });
      return new THREE.Points(geometry, material);
    }

    function makeCameraObjects(data, reference=false) {
      const centerGeometry = new THREE.BufferGeometry();
      centerGeometry.setAttribute('position', new THREE.Float32BufferAttribute(data.cameras.centers.flat(), 3));
      const centerMaterial = new THREE.PointsMaterial({
        size: reference ? 0.34 : 0.45,
        color: reference ? 0xdce3ea : 0x52c7b8,
        transparent: reference,
        opacity: reference ? 0.62 : 1.0,
        sizeAttenuation: true,
      });
      const centers = new THREE.Points(centerGeometry, centerMaterial);
      const baseFrustumPositions = new Float32Array(data.cameras.frustumVertices.flat());
      const frustumGeometry = new THREE.BufferGeometry();
      frustumGeometry.setAttribute('position', new THREE.BufferAttribute(baseFrustumPositions.slice(), 3));
      const frustumMaterial = new THREE.LineBasicMaterial({
        color: reference ? 0xdce3ea : 0x52c7b8,
        transparent: true,
        opacity: reference ? 0.32 : state.cameraOpacity,
      });
      const frustums = new THREE.LineSegments(frustumGeometry, frustumMaterial);
      return { centers, frustums, baseFrustumPositions };
    }

    function setFrustumScale(handles, scale) {
      if (!handles) return;
      const attribute = handles.frustums.geometry.getAttribute('position');
      const positions = attribute.array;
      const base = handles.baseFrustumPositions;
      const floatsPerFrustum = 16 * 3;
      for (let offset = 0; offset < base.length; offset += floatsPerFrustum) {
        const centerX = base[offset];
        const centerY = base[offset + 1];
        const centerZ = base[offset + 2];
        const end = Math.min(offset + floatsPerFrustum, base.length);
        for (let index = offset; index < end; index += 3) {
          positions[index] = centerX + (base[index] - centerX) * scale;
          positions[index + 1] = centerY + (base[index + 1] - centerY) * scale;
          positions[index + 2] = centerZ + (base[index + 2] - centerZ) * scale;
        }
      }
      attribute.needsUpdate = true;
      handles.frustums.geometry.computeBoundingSphere();
    }

    function makeGcpGroup(data, reference=false) {
      const group = new THREE.Group();
      const geometry = new THREE.BufferGeometry();
      geometry.setAttribute('position', new THREE.Float32BufferAttribute(data.gcps.map(g => g.position).flat(), 3));
      const material = new THREE.PointsMaterial({
        size: reference ? 0.9 : 1.3,
        color: reference ? 0xf2f4f7 : 0xffcc66,
        transparent: reference,
        opacity: reference ? 0.62 : 1.0,
        sizeAttenuation: true,
      });
      group.add(new THREE.Points(geometry, material));
      return group;
    }

    function buildDatasetGroup(data, reference=false) {
      const group = new THREE.Group();
      const pointCloud = makePointCloud(data, reference);
      const cameraObjects = makeCameraObjects(data, reference);
      const gcpGroup = makeGcpGroup(data, reference);
      group.add(pointCloud);
      group.add(cameraObjects.centers);
      group.add(cameraObjects.frustums);
      group.add(gcpGroup);
      return {
        group,
        pointCloud,
        pointMaterial: pointCloud.material,
        cameraCenters: cameraObjects.centers,
        frustums: cameraObjects.frustums,
        frustumMaterial: cameraObjects.frustums.material,
        baseFrustumPositions: cameraObjects.baseFrustumPositions,
        gcpGroup,
        reference,
      };
    }

    function applyVisibility() {
      if (activeHandles) {
        activeHandles.pointCloud.visible = state.points;
        activeHandles.cameraCenters.visible = state.cameras;
        activeHandles.frustums.visible = state.cameras;
        activeHandles.gcpGroup.visible = state.gcps;
        activeHandles.pointMaterial.size = state.pointSize;
        activeHandles.frustumMaterial.opacity = state.cameraOpacity;
        setFrustumScale(activeHandles, state.frustumScale);
      }
      if (referenceHandles) {
        const showReference = state.reference && activeIndex !== referenceIndex;
        referenceHandles.group.visible = showReference;
        referenceHandles.pointCloud.visible = showReference && state.points;
        referenceHandles.cameraCenters.visible = showReference && state.cameras;
        referenceHandles.frustums.visible = showReference && state.cameras;
        referenceHandles.gcpGroup.visible = showReference && state.gcps;
        referenceHandles.pointMaterial.size = state.pointSize * 0.75;
        setFrustumScale(referenceHandles, state.frustumScale);
      }
    }

    function setActive(index) {
      activeIndex = index;
      if (activeGroup) scene.remove(activeGroup);
      activeHandles = buildDatasetGroup(datasets[index], false);
      activeGroup = activeHandles.group;
      scene.add(activeGroup);
      updateButtons();
      updateStats();
      updateGcps();
      applyVisibility();
    }

    function updateButtons() {
      document.querySelectorAll('.quality-button').forEach((button) => {
        const index = Number(button.dataset.index);
        button.classList.toggle('active', index === activeIndex);
      });
    }

    function updateStats() {
      const data = datasets[activeIndex];
      const stats = data.stats;
      const rows = [
        ['Level', data.label],
        ['Group', data.group],
        ['Images', stats.images],
        ['Points', stats.points],
        ['Obs.', stats.observations],
        ['GCPs', stats.gcps],
        ['RMSE px', fmt(stats.rmsePx, 3)],
        ['Target px', fmt(stats.targetRmsePx, 2)],
        ['Median px', fmt(stats.medianPx, 3)],
        ['P95 px', fmt(stats.p95Px, 3)],
        ['Max px', fmt(stats.maxPx, 1)],
        ['Neg. depth', stats.negativeDepthCount],
        ['GCP RMSE', fmt(stats.gcpRmsePx, 3)],
        ['Pose scale', fmt(stats.poseScale, 6)],
      ];
      const panel = document.getElementById('stats');
      panel.innerHTML = '';
      for (const [label, value] of rows) {
        const node = document.createElement('div');
        node.className = 'stat';
        node.innerHTML = `<span>${label}</span><strong>${value ?? 'n/a'}</strong>`;
        panel.appendChild(node);
      }
    }

    function updateGcps() {
      const data = datasets[activeIndex];
      const gcpList = document.getElementById('gcps');
      gcpList.innerHTML = '';
      gcpList.className = '';
      if (data.gcps.length === 0) {
        gcpList.textContent = 'No GCP sidecar files found.';
        gcpList.className = 'empty';
        return;
      }
      for (const gcp of data.gcps) {
        const node = document.createElement('div');
        node.className = 'gcp';
        node.innerHTML = `<span class="badge">${gcp.name}</span><span>${gcp.observations} observations</span><span>${gcp.isCheckPoint ? 'check' : 'control'}</span>`;
        gcpList.appendChild(node);
      }
    }

    function buildQualityPanel() {
      const panel = document.getElementById('qualityPanel');
      for (const groupName of payload.groups) {
        const groupItems = datasets
          .map((dataset, index) => ({ dataset, index }))
          .filter(item => item.dataset.group === groupName);
        if (groupItems.length === 0) continue;
        const title = document.createElement('h2');
        title.textContent = groupName;
        panel.appendChild(title);
        const grid = document.createElement('div');
        grid.className = 'quality-grid';
        for (const item of groupItems) {
          const button = document.createElement('button');
          button.className = `quality-button ${groupName === 'Stress Test' ? 'stress' : ''}`;
          button.dataset.index = item.index;
          button.textContent = item.dataset.label;
          button.addEventListener('click', () => setActive(item.index));
          grid.appendChild(button);
        }
        panel.appendChild(grid);
      }
    }

    function resetView(top=false) {
      controls.target.copy(center);
      if (top) {
        camera.position.set(center.x, center.y + diagonal * 1.35, center.z + 0.001);
      } else {
        camera.position.set(center.x - diagonal * 0.65, center.y + diagonal * 0.45, center.z + diagonal * 0.75);
      }
      camera.near = Math.max(diagonal / 100000, 0.001);
      camera.far = Math.max(diagonal * 20, 1000);
      camera.updateProjectionMatrix();
      controls.update();
    }

    buildQualityPanel();
    referenceHandles = buildDatasetGroup(datasets[referenceIndex], true);
    referenceGroup = referenceHandles.group;
    referenceGroup.visible = false;
    scene.add(referenceGroup);
    setActive(referenceIndex);

    document.getElementById('togglePoints').addEventListener('change', event => { state.points = event.target.checked; applyVisibility(); });
    document.getElementById('toggleCameras').addEventListener('change', event => { state.cameras = event.target.checked; applyVisibility(); });
    document.getElementById('toggleGcps').addEventListener('change', event => { state.gcps = event.target.checked; applyVisibility(); });
    document.getElementById('toggleReference').addEventListener('change', event => { state.reference = event.target.checked; applyVisibility(); });
    document.getElementById('pointSize').addEventListener('input', event => { state.pointSize = Number(event.target.value); applyVisibility(); });
    document.getElementById('cameraOpacity').addEventListener('input', event => { state.cameraOpacity = Number(event.target.value); applyVisibility(); });
    document.getElementById('frustumScale').addEventListener('input', event => { state.frustumScale = Number(event.target.value); applyVisibility(); });
    document.getElementById('resetView').addEventListener('click', () => resetView(false));
    document.getElementById('topView').addEventListener('click', () => resetView(true));

    window.addEventListener('resize', () => {
      camera.aspect = window.innerWidth / window.innerHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(window.innerWidth, window.innerHeight);
    });

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
    payload = build_quality_payload(
        args.input_root.resolve(),
        args.max_points,
        args.camera_stride,
        args.stress_threshold,
        args.reference_dir.resolve() if args.reference_dir else None,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    html = HTML_TEMPLATE.replace("__PAYLOAD__", json.dumps(payload, separators=(",", ":")))
    args.output.write_text(html, encoding="utf-8", newline="\n")
    print(f"Wrote linked PVL-BA quality viewer to {args.output.resolve()}")
    print(f"  source: {payload['source']}")
    print(f"  quality levels: {len(payload['datasets'])}")
    for dataset in payload["datasets"]:
        stats = dataset["stats"]
        gcp_rmse = stats.get("gcpRmsePx")
        gcp_text = "n/a" if gcp_rmse is None else f"{gcp_rmse:.6f}"
        print(
            f"  {dataset['label']}: rmse={stats['rmsePx']:.6f} px "
            f"gcp_rmse={gcp_text} neg_depth={stats['negativeDepthCount']}"
        )


if __name__ == "__main__":
    main()
