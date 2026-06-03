# PVL-BA-Bench

Large-scale bundle adjustment benchmark datasets and conversion tools from **Polar-vision Lab**.

This repository hosts the public tools and documentation for **PVL-BA-Bench**. The benchmark is designed for large-scale bundle adjustment and photogrammetric optimization research. The planned release contains 500 image blocks, including at least 100 ultra-large blocks with more than 50,000 images each. Some blocks include ground control points (GCPs).

## Naming

- **PVL-BA-Bench**: the benchmark project and GitHub repository.
- **PVL-BA500**: the planned 500-block benchmark dataset.
- **PVL-BA**: the Polar-vision Lab four-file bundle adjustment data format.

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

Generate an interactive browser viewer for COLMAP text BA datasets:

```powershell
powershell -ExecutionPolicy Bypass -File tools\ba_visualizer\run.ps1 `
  -InputDir path\to\COLMAP_TEXT_MODEL `
  -OutputHtml path\to\viewer.html
```

The viewer renders sparse 3D points, camera centers, camera frustums, and optional `gcp.txt` / `gcp_observations.txt` sidecars. See [tools/ba_visualizer](tools/ba_visualizer) for details.

## Input Format

`AT.xml` files are treated as **BlocksExchange XML** inputs. This format is commonly associated with Bentley ContextCapture / iTwin Capture orientation exports and is also produced by compatible photogrammetry software, including Daspatial Reconstruction Master outputs.

When GCPs are present, the conversion tools require `pyproj` for cross-SRS coordinate transforms:

```powershell
pip install pyproj
```

## Repository Status

This repository currently contains tools and format documentation. Dataset download links and benchmark protocols will be added with the public PVL-BA500 release.

## Citation

Citation information will be added when the benchmark paper or technical report is released.
