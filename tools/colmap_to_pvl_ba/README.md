# COLMAP to PVL-BA

Convert a COLMAP text model into PVL-BA format:

```text
cameras.txt images.txt points3D.txt
```

to:

```text
cal.txt
Cam-<num_images>-.txt
XYZ.txt
Feature.txt
```

## Notes

- The converter currently targets COLMAP text models.
- `OPENCV` cameras are supported and are exported to PVL-BA by writing only `fx, fy, cx, cy` to `cal.txt`.
- COLMAP image measurements are distorted for `OPENCV` cameras. PVL-BA stores undistorted coordinates, so observations are undistorted before writing `Feature.txt`.
- PVL-BA image indices are 0-based row indices. By default this tool uses `IMAGE_ID - 1`, which matches standard ordered COLMAP exports such as the FiveLens sample. Use `--image-index row` to use image row order instead.

## Usage

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1 `
  -InputDir path\to\COLMAP_TEXT_MODEL `
  -OutputDir path\to\PVL_BA_OUTPUT
```

Verify:

```powershell
python ..\atxml_to_pvl_ba\verify_pvl_ba.py --input-dir path\to\PVL_BA_OUTPUT
```

