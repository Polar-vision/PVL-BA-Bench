# PVL-BA Format

PVL-BA is the Polar-vision Lab four-file bundle adjustment data format. It stores each BA problem in plain text using four files:

```text
cal.txt
Cam-<num_images>-.txt
XYZ.txt
Feature.txt
```

## Intrinsics: `cal.txt`

`cal.txt` contains one 3x3 intrinsic matrix per camera group. Each group uses three lines:

```text
fx 0  cx
0  fy cy
0  0  1
```

The group index is 1-based in camera files.

## Camera Poses: `Cam-<num_images>-.txt`

The camera file contains one image pose per line:

```text
ey ex ez Xc Yc Zc camera_id
```

where:

- `ey`: rotation around the y-axis, in radians.
- `ex`: rotation around the x-axis, in radians.
- `ez`: rotation around the z-axis, in radians.
- `Xc Yc Zc`: perspective center / camera center in world coordinates.
- `camera_id`: 1-based intrinsic group id in `cal.txt`.

The Euler angles construct the world-to-camera rotation matrix as:

```text
c1 = cos(ey), c2 = cos(ex), c3 = cos(ez)
s1 = sin(ey), s2 = sin(ex), s3 = sin(ez)

R[0] =  c1*c3 - s1*s2*s3
R[1] =  c2*s3
R[2] =  s1*c3 + c1*s2*s3

R[3] = -c1*s3 - s1*s2*c3
R[4] =  c2*c3
R[5] = -s1*s3 + c1*s2*c3

R[6] = -s1*c2
R[7] = -s2
R[8] =  c1*c2
```

Projection uses:

```text
X_cam = R * (X_world - C)
u = fx * X_cam.x / X_cam.z + cx
v = fy * X_cam.y / X_cam.z + cy
```

## 3D Points: `XYZ.txt`

`XYZ.txt` contains one 3D object point per line:

```text
X Y Z
```

Line `i` in `XYZ.txt` corresponds to line `i` in `Feature.txt`.

## Feature Tracks: `Feature.txt`

Each line contains the observations of the corresponding 3D point:

```text
N image_idx_1 u_1 v_1 image_idx_2 u_2 v_2 ...
```

where:

- `N`: number of observations in the track.
- `image_idx`: 0-based image index, matching the row index in `Cam-<num_images>-.txt`.
- `u v`: undistorted image coordinates in pixels.

## Distortion

PVL-BA stores undistorted image measurements and does not include distortion parameters in the four-file problem. If the source data contains distorted measurements, they should be undistorted before writing `Feature.txt`.

