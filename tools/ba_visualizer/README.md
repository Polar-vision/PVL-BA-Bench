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

## Display

- Sparse 3D points are rendered with their COLMAP RGB colors when available.
- Camera centers and frustums are rendered from COLMAP `qvec/tvec`.
- GCP coordinates are rendered in the same world frame as the BA problem.
- The viewer maps ENU-style world coordinates to a Z-up scene for easier inspection.
- For PVL-BA quality variants, the side panel displays current RMSE and target RMSE from `noise_metadata.json`.
