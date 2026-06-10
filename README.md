# PVL-BA-Bench

Large-scale bundle adjustment benchmark datasets and conversion tools from **Polar-vision Lab**.

This repository hosts the public tools and documentation for **PVL-BA-Bench**. The benchmark is designed for large-scale bundle adjustment and photogrammetric optimization research. The planned release contains 500 image blocks, including at least 100 ultra-large blocks with more than 50,000 images each. Some blocks include ground control points (GCPs).

## Naming

- **PVL-BA-Bench**: the benchmark project and GitHub repository.
- **PVL-BA500**: the planned 500-block benchmark dataset.
- **PVL-BA**: the Polar-vision Lab four-file bundle adjustment data format.

Dataset instances use explicit count tags:

```text
problem-i<images>-p<points>-o<observations>-g<gcps>
```

`g` counts only GCPs with at least one measurement in the filtered BA image set. When a dataset name includes `-c<checkpoints>`, `c` uses the same observed-GCP filtering.

For controlled-quality variants, append the target initial RMSE:

```text
problem-i507-p40315-o139534-g3-init-rmse050p00px
```

This is preferred over bare positional names such as `problem-507-40315-139534-3`, because each count remains self-describing.

## PVL-BA Format

PVL-BA stores one BA problem as four text files:

```text
cal.txt
Cam-<num_images>-.txt
XYZ.txt
Feature.txt
```

The format separates camera intrinsics, camera poses, 3D points, and feature tracks. See [docs/PVL-BA-format.md](docs/PVL-BA-format.md) for the full specification.

## Tools

## Sample Data

A small BlocksExchange XML sample is included for testing the converters:

```text
samples/blocks_exchange/sample_at.xml
```

It contains 8 images, 30 tie points, and 105 observations. Example:

```powershell
powershell -ExecutionPolicy Bypass -File tools\atxml_to_pvl_ba\run.ps1 `
  -InputXml samples\blocks_exchange\sample_at.xml `
  -OutputDir outputs\sample_pvl_ba
```

### BlocksExchange XML to PVL-BA

Convert Bentley ContextCapture / iTwin Capture compatible `BlocksExchange XML` files, commonly named `AT.xml`, into PVL-BA:

```powershell
powershell -ExecutionPolicy Bypass -File tools\atxml_to_pvl_ba\run.ps1 `
  -InputXml path\to\AT.xml `
  -OutputDir path\to\PVL_BA_OUTPUT
```

Verify the exported PVL-BA problem:

```powershell
python tools\atxml_to_pvl_ba\verify_pvl_ba.py --input-dir path\to\PVL_BA_OUTPUT
```

The converter undistorts source image measurements before writing `Feature.txt`, because PVL-BA stores undistorted image coordinates.

Photos without a complete `Pose` are treated as invalid and skipped. Tie point and GCP measurements that reference skipped photos are filtered out; tracks with fewer than two remaining tie observations are omitted.

The AT converters use streaming XML parsing. `Photogroup`, `TiePoint`, and `ControlPoint` elements are processed incrementally, so large BlocksExchange XML files do not need to be loaded as one in-memory tree.

Blocks with multiple `Photogroup` entries are supported. Each photogroup is exported as a separate intrinsic group in `cal.txt`, and each image row in `Cam-<N>-.txt` stores the corresponding 1-based camera group ID.

If `AT.xml` contains GCPs, the converter also writes:

```text
gcp.txt
gcp_observations.txt
```

GCP coordinates are transformed into the BA coordinate system. For local ENU blocks, EPSG source coordinates are converted to geodetic coordinates with `pyproj` and then to the target ENU frame. If the ENU definition omits origin height, the tools use height `0` for the ENU origin.

### BlocksExchange XML to COLMAP

An auxiliary converter to COLMAP text format is also included:

```powershell
powershell -ExecutionPolicy Bypass -File tools\atxml_to_colmap\run.ps1 `
  -InputXml path\to\AT.xml `
  -OutputDir path\to\COLMAP_TEXT_MODEL
```

See [tools/atxml_to_colmap](tools/atxml_to_colmap) for details.

For COLMAP exports, `gcp.txt` and `gcp_observations.txt` are written as sidecar files when GCPs are present. The GCP observations remain in distorted pixel coordinates to match the COLMAP camera model.

Multiple `Photogroup` entries are exported as multiple COLMAP cameras in `cameras.txt`; each image references its source photogroup's camera ID.

### BlocksExchange XML to BAL

Convert BlocksExchange XML files to the classic BAL single-file format:

```powershell
powershell -ExecutionPolicy Bypass -File tools\atxml_to_bal\run.ps1 `
  -InputXml path\to\AT.xml `
  -OutputBal path\to\problem.bal `
  -Mode normalized
```

See [tools/atxml_to_bal](tools/atxml_to_bal) for details.

For BAL exports, GCP sidecars are written next to the BAL file as `problem.gcp.txt` and `problem.gcp_observations.txt`. Their observation coordinates follow the selected BAL mode (`normalized` or `pixel`).

For multi-photogroup inputs, each BAL camera keeps the intrinsic scale of its source photogroup.

### COLMAP to PVL-BA

Convert COLMAP text models to PVL-BA:

```powershell
powershell -ExecutionPolicy Bypass -File tools\colmap_to_pvl_ba\run.ps1 `
  -InputDir path\to\COLMAP_TEXT_MODEL `
  -OutputDir path\to\PVL_BA_OUTPUT
```

For COLMAP `OPENCV` cameras, observations are undistorted before writing `Feature.txt`.

See [tools/colmap_to_pvl_ba](tools/colmap_to_pvl_ba) for details.

### BA Dataset Visualizer

Generate an interactive browser viewer for COLMAP text or PVL-BA datasets:

```powershell
powershell -ExecutionPolicy Bypass -File tools\ba_visualizer\run.ps1 `
  -InputDir path\to\BA_DATASET `
  -OutputHtml path\to\viewer.html
```

The viewer renders sparse 3D points, camera centers, camera frustums, optional `gcp.txt` / `gcp_observations.txt` sidecars, and PVL-BA noise metadata when present. See [tools/ba_visualizer](tools/ba_visualizer) for details.

For PVL-BA quality variants, generate a linked viewer that switches between `original`, the main benchmark levels, and stress-test levels in one browser window:

```powershell
powershell -ExecutionPolicy Bypass -File tools\ba_visualizer\run_quality.ps1 `
  -InputRoot path\to\QUALITY_VARIANTS `
  -OutputHtml path\to\quality_linked_viewer.html
```

The linked viewer preserves the current 3D camera view while switching quality levels and can overlay the original reference geometry. It reports per-level reprojection RMSE, residual percentiles, GCP RMSE, and negative-depth counts.

For initialization-quality variants, the viewer also reports camera-center, camera-rotation, and tie-point world-coordinate error summaries from `noise_metadata.json`. These values are useful for interpreting stress tests: a visually modest frustum or 3D point displacement can still produce a very large image-space RMSE when projected through long focal lengths.

For a release directory that already contains multiple viewer HTML files, generate a local index page for switching between datasets:

```powershell
powershell -ExecutionPolicy Bypass -File tools\ba_visualizer\run_index.ps1 `
  -ViewerDir outputs\control_at_20_release\viewers `
  -Manifest manifests\control_at_20.csv
```

This writes `viewers\index.html` next to the dataset viewers.

### PVL-BA Quality Variants

Generate noisy PVL-BA datasets with controlled initial RMSE:

```powershell
powershell -ExecutionPolicy Bypass -File tools\pvl_ba_quality\run.ps1 `
  -InputDir path\to\PVL_BA_OUTPUT `
  -OutputRoot path\to\QUALITY_VARIANTS
```

The default main preset generates `2, 5, 10, 20, 50, 100 px` quality variants; use `-Preset stress` for `200, 500 px`. The default `auto` mode uses pose perturbation plus re-triangulated tie points for main levels, and joint camera-pose plus 3D point initialization perturbation for stress levels. Both modes stream `XYZ.txt` and `Feature.txt` point-by-point and use sampled scale solving by default for large blocks. Use `-Mode observation-noise` only for supplementary image-measurement noise experiments. See [tools/pvl_ba_quality](tools/pvl_ba_quality) for details.

### Controlled-AT Release Manifest

The controlled aerotriangulation blocks with GCPs are listed in:

```text
manifests/control_at_20.csv
```

This manifest contains 20 unique BA problems after removing duplicate Shiyan first-batch exports. Use [tools/publish_control_at](tools/publish_control_at) to batch-publish PVL-BA, COLMAP, BAL, quality variants, linked viewers, and dashboard/index pages.

The ABS Cloud Earth blocks are listed in:

```text
manifests/abs_12.csv
```

This manifest contains 12 valid ABS BA problems. The manifest and generated metadata include `source_software_vendor` and `source_software` fields to keep the Cloud Earth provenance separate from Reconstruction Master exports.

The REL Cloud Earth blocks without GCPs are listed in:

```text
manifests/rel.csv
```

This manifest contains 96 valid REL BA problems from 99 Cloud Earth exports after filtering blocks with suspicious intrinsics or geometry. REL blocks have no GCPs, so the released dataset names and metadata use `g0-c0`.

The COLMAP text-model BA blocks are listed in:

```text
manifests/ba_colmap_122.csv
```

This manifest contains 122 COLMAP `cameras.txt/images.txt/points3D.txt` models from `BA/Colmap_Output`. These blocks have no GCPs, so released dataset names and metadata use `g0-c0`.

## Input Format

`AT.xml` files are treated as **BlocksExchange XML** inputs. This format is commonly associated with Bentley ContextCapture / iTwin Capture orientation exports and is also produced by compatible photogrammetry software, including Daspatial Reconstruction Master and Cloud Earth outputs.

When GCPs are present, the conversion tools require `pyproj` for cross-SRS coordinate transforms:

```powershell
pip install pyproj
```

## Repository Status

This repository currently contains tools and format documentation. Dataset download links and benchmark protocols will be added with the public PVL-BA500 release.

## Citation

Citation information will be added when the benchmark paper or technical report is released.
