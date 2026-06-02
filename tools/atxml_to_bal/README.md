# BlocksExchange XML to BAL

Convert BlocksExchange XML files, commonly named `AT.xml`, to the classic BAL single-file format used by the Ceres Solver examples.

## BAL Layout

The output file uses:

```text
num_cameras num_points num_observations
camera_index point_index x y
...
camera_0_parameters
...
point_0_xyz
...
```

Each camera has 9 parameters:

```text
angle_axis_x angle_axis_y angle_axis_z
translation_x translation_y translation_z
focal
k1
k2
```

## Coordinate Modes

`AT.xml` measurements are distorted pixel coordinates. The converter undistorts observations before writing BAL.

Default mode:

```text
--mode normalized
```

writes observations in normalized camera coordinates:

```text
x = (u_undistorted - cx) / f
y = (v_undistorted - cy) / f
```

and writes `focal = 1, k1 = 0, k2 = 0`.

Pixel mode:

```text
--mode pixel
```

writes centered undistorted pixel observations:

```text
x = u_undistorted - cx
y = v_undistorted - cy
```

and writes `focal = FocalLengthPixels, k1 = 0, k2 = 0`.

## Usage

```powershell
powershell -ExecutionPolicy Bypass -File .\run.ps1 `
  -InputXml path\to\AT.xml `
  -OutputBal path\to\problem.bal
```

Verify:

```powershell
python .\verify_bal.py --input path\to\problem.bal
```
