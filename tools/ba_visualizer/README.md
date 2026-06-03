# BA Visualizer

Generate a standalone interactive HTML viewer for bundle adjustment datasets.

The first supported input is a COLMAP text model directory:

```text
cameras.txt
images.txt
points3D.txt
```

If `gcp.txt` and `gcp_observations.txt` sidecars are present, GCPs are displayed as highlighted markers and listed in the side panel.

## Usage

```powershell
python .\generate_viewer.py `
  --input-dir ..\..\outputs\abs_at_colmap_check `
  --output ..\..\outputs\abs_at_colmap_check_viewer.html
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

- Sparse 3D points are rendered with their COLMAP RGB colors.
- Camera centers and frustums are rendered from COLMAP `qvec/tvec`.
- GCP coordinates are rendered in the same world frame as the BA problem.
- The viewer maps ENU-style world coordinates to a Z-up scene for easier inspection.
