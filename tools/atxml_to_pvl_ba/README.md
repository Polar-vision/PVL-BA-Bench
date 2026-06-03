# AT.xml to PVL-BA

Convert BlocksExchange `AT.xml` into **PVL-BA**, the Polar-vision Lab four-file bundle adjustment format:

```text
cal.txt
Cam-<num_images>-.txt
XYZ.txt
Feature.txt
```

The converter uses the Python standard library for non-GCP blocks. If GCPs must be transformed between coordinate systems, install `pyproj`.

## Target Format

`cal.txt` writes one 3x3 intrinsic matrix per camera group:

```text
fx 0 cx
0 fy cy
0 0 1
```

`Cam-<N>-.txt` writes one image pose per row:

```text
ey ex ez Xc Yc Zc camera_id
```

where `camera_id` is 1-based and indexes the intrinsics blocks in `cal.txt`.

`XYZ.txt` writes one 3D point per row:

```text
X Y Z
```

`Feature.txt` writes one feature track per 3D point:

```text
N image_idx u v image_idx u v ...
```

where `image_idx` is 0-based and matches the row index in `Cam-<N>-.txt`.

## Pose Convention

The source `AT.xml` was verified to use:

```text
X_cam = R * (X_world - C)
```

The PVL-BA Euler matrix is:

```text
R[0]=c1*c3-s1*s2*s3;     R[1]=c2*s3;     R[2]=s1*c3+c1*s2*s3;
R[3]=-c1*s3-s1*s2*c3;    R[4]=c2*c3;     R[5]=-s1*s3+c1*s2*c3;
R[6]=-s1*c2;             R[7]=-s2;       R[8]=c1*c2;
```

with:

```text
ey = euler_angles[0]
ex = euler_angles[1]
ez = euler_angles[2]
```

## Important

`AT.xml` tie point measurements are distorted pixel coordinates. This converter undistorts each measurement before writing `Feature.txt`, because the target four-file format uses already-undistorted coordinates.

Photos without a complete `Pose` are skipped. Measurements that reference skipped photos are filtered out, and output image indices are compact 0-based row indices after filtering.

## GCP Extension

If the input contains `<ControlPoints>`, the converter writes:

```text
gcp.txt
gcp_observations.txt
```

GCP object coordinates are transformed into the block SRS so they can be optimized with the ENU BA problem. For `EPSG:*` GCP coordinates and an `ENU:lat,lon[,h]` block SRS, the converter uses `pyproj` followed by an ECEF-to-ENU transform. GCP image observations are undistorted pixel coordinates, matching `Feature.txt`.

## Usage

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1 `
  -InputXml path\to\AT.xml `
  -OutputDir path\to\PVL_BA_OUTPUT
```

Verify an exported dataset:

```powershell
python .\verify_pvl_ba.py --input-dir path\to\PVL_BA_OUTPUT
```

The verifier reports tie point residuals and, when sidecar files exist, GCP residuals.
