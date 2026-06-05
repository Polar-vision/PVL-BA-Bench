# BA Visualizer

Generate a standalone interactive HTML viewer for bundle adjustment datasets.

Supported inputs:

- COLMAP text model directory.
- PVL-BA directory.

COLMAP input:

```text
cameras.txt
images.txt
points3D.txt
```

PVL-BA input:

```text
cal.txt
Cam-<num_images>-.txt
XYZ.txt
Feature.txt
```

If `gcp.txt` and `gcp_observations.txt` sidecars are present, GCPs are displayed as highlighted markers and listed in the side panel.

## Usage

```powershell
python .\generate_viewer.py `
  --input-dir ..\..\outputs\abs_at_colmap_check `
  --output ..\..\outputs\abs_at_colmap_check_viewer.html
```

PVL-BA example:

```powershell
python .\generate_viewer.py `
  --input-dir ..\..\outputs\abs_at_pvl_ba_check `
  --output ..\..\outputs\abs_at_pvl_ba_check_viewer.html `
  --format pvl-ba
```

Optional controls:

```powershell
python .\generate_viewer.py `
  --input-dir path\to\COLMAP_TEXT_MODEL `
  --output path\to\viewer.html `
  --max-points 100000 `
  --camera-stride 1
```

Open the generated HTML file in a browser. The viewer uses Three.js from a CDN, so the browser needs network access the first time it loads the page.

## Linked PVL-BA Quality Viewer

For controlled-quality PVL-BA variants, generate a single linked viewer from the directory that contains the `original`, main benchmark, and stress-test datasets:

```powershell
powershell -ExecutionPolicy Bypass -File .\run_quality.ps1 `
  -InputRoot ..\..\outputs\abs_at_quality_pvl_ba `
  -ReferenceDir ..\..\outputs\abs_at_quality_pvl_ba\problem-i507-p40315-o139534-g3-original `
  -OutputHtml ..\..\outputs\abs_at_quality_pvl_ba\quality_linked_viewer.html
```

The linked viewer automatically discovers directories named like:

```text
problem-i507-p40315-o139534-g3-original
problem-i507-p40315-o139534-g3-init-rmse002p00px
problem-i507-p40315-o139534-g3-init-rmse500p00px
```

It groups `2, 5, 10, 20, 50, 100 px` as the main benchmark and `200, 500 px` as stress tests by default. Switching quality levels preserves the current 3D view and can overlay the original reference geometry for comparison.

Use `--reference-dir` / `-ReferenceDir` when the original PVL-BA dataset is stored outside the quality-variant directory.

## Display

- Sparse 3D points are rendered with their COLMAP RGB colors when available.
- Camera centers and frustums are rendered from COLMAP `qvec/tvec`.
- GCP coordinates are rendered in the same world frame as the BA problem.
- The viewer maps ENU-style world coordinates to a Z-up scene for easier inspection.
- For PVL-BA quality variants, the side panel displays current RMSE and target RMSE from `noise_metadata.json`.
- The linked quality viewer also reports per-level RMSE, residual percentiles, GCP RMSE, negative-depth counts, and pose-triangulation error summaries when available.
- Use the `Frustum size` slider to resize camera frustums interactively without regenerating the viewer. The default frustum scale is the minimum slider value, and the default camera-frustum opacity is 50%.

`Original overlay` is a visual reference, not an image-space residual plot. In high-RMSE stress tests, camera centers and frustums can still appear close in world space while small angular errors, long focal lengths, and re-triangulated tie points produce hundreds of pixels of reprojection error. Use the side-panel camera-center RMSE, camera-rotation RMSE, tie-point RMSE, and residual percentiles together.
