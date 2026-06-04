# AT.xml to COLMAP

Convert a BlocksExchange `AT.xml` aerotriangulation result into COLMAP text model files:

- `cameras.txt`
- `images.txt`
- `points3D.txt`

The converter uses the Python standard library for non-GCP blocks. If GCPs must be transformed between coordinate systems, install `pyproj`.

The XML reader is streaming: photogroups, tie points, and control points are processed incrementally with `xml.etree.ElementTree.iterparse`. For COLMAP, per-image `POINTS2D` records are staged in temporary files while `points3D.txt` is written from the tie-point stream, avoiding a large in-memory observation table.

## Pose convention

Validation on the sample BlocksExchange XML problem used during development shows the XML rotation is world-to-camera:

```text
X_cam = R * (X_world - C)
```

The tested reprojection error for `R * (X - C)` is about 1.35 px mean on 20,000 sampled measurements. The transpose convention is orders of magnitude worse.

For COLMAP, the script writes:

```text
R_colmap = R_xml
t_colmap = -R_xml * C_xml
```

## Camera model

By default the script writes COLMAP `OPENCV` cameras:

```text
fx fy cx cy k1 k2 p1 p2
```

`AT.xml` also contains `K3`. COLMAP's standard `OPENCV` model does not store `K3`, so the default export drops it. Use `--camera-model FULL_OPENCV` to preserve `K3`:

```text
fx fy cx cy k1 k2 k3 k4 k5 k6 p1 p2
```

where `k4..k6` are written as zero.

## Usage

```powershell
.\run.ps1 -InputXml path\to\AT.xml -OutputDir path\to\COLMAP_TEXT_MODEL
```

Or directly:

```powershell
python .\atxml_to_colmap.py --input path\to\AT.xml --output path\to\COLMAP_TEXT_MODEL
```

Use full distortion export:

```powershell
python .\atxml_to_colmap.py --input path\to\AT.xml --output path\to\COLMAP_TEXT_MODEL_FULL --camera-model FULL_OPENCV
```

## Invalid Photos and GCPs

Photos without a complete `Pose` are skipped. Measurements that reference skipped photos are filtered out, and COLMAP `IMAGE_ID` values are compact positive IDs after filtering.

Multiple `Photogroup` entries are supported. Each photogroup is exported as one COLMAP camera, and each image row references its source photogroup's camera ID.

If the input contains `<ControlPoints>`, the converter writes sidecar files:

```text
gcp.txt
gcp_observations.txt
```

GCP object coordinates are transformed into the block SRS so they can be optimized with the local ENU BA problem. GCP image observations remain distorted pixel coordinates, matching the COLMAP camera model written in `cameras.txt`.

Verify GCP sidecars:

```powershell
python .\verify_colmap_gcp.py --input-dir path\to\COLMAP_TEXT_MODEL_FULL
```
