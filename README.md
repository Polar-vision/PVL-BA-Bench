# PVL-BA-Bench

Large-scale bundle adjustment benchmark datasets and conversion tools from **Polar-vision Lab**.

This repository hosts the public tools and documentation for **PVL-BA-Bench**. The benchmark is designed for large-scale bundle adjustment, SfM, and photogrammetry research. The planned release contains 500 image blocks, including at least 100 ultra-large blocks with more than 50,000 images each. Some blocks include ground control points (GCPs).

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

### BlocksExchange XML to COLMAP

An auxiliary converter to COLMAP text format is also included:

```powershell
powershell -ExecutionPolicy Bypass -File tools\atxml_to_colmap\run.ps1 `
  -InputXml path\to\AT.xml `
  -OutputDir path\to\COLMAP_TEXT_MODEL
```

See [tools/atxml_to_colmap](tools/atxml_to_colmap) for details.

## Input Format

`AT.xml` files are treated as **BlocksExchange XML** inputs. This format is commonly associated with Bentley ContextCapture / iTwin Capture orientation exports and is also produced by compatible photogrammetry software.

## Repository Status

This repository currently contains tools and format documentation. Dataset download links and benchmark protocols will be added with the public PVL-BA500 release.

## Citation

Citation information will be added when the benchmark paper or technical report is released.

