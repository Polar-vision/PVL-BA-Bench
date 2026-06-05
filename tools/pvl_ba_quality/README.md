# PVL-BA Quality Variants

Generate PVL-BA quality variants with controlled initial reprojection RMSE.

## Naming

Use explicit field tags instead of bare positional numbers:

```text
problem-i<images>-p<points>-o<observations>-g<gcps>
```

For default initialization-quality variants, append the target initial RMSE:

```text
problem-i507-p40315-o139534-g3-init-rmse002p00px
problem-i507-p40315-o139534-g3-init-rmse005p00px
problem-i507-p40315-o139534-g3-init-rmse010p00px
problem-i507-p40315-o139534-g3-init-rmse020p00px
problem-i507-p40315-o139534-g3-init-rmse050p00px
problem-i507-p40315-o139534-g3-init-rmse100p00px
```

Observation-noise variants use `obs-rmse` instead:

```text
problem-i507-p40315-o139534-g3-obs-rmse010p00px
```

The tags make the name self-describing:

- `i`: valid network images.
- `p`: 3D tie points.
- `o`: tie-point image observations.
- `g`: GCP count.

## Modes

Default mode:

```text
--mode init-pose-triangulate
```

This mode perturbs camera poses, keeps `Feature.txt` fixed, and re-triangulates `XYZ.txt` from the perturbed cameras and original image observations. It is the recommended benchmark mode because the 3D geometry, camera initialization, and visualized structure all reflect the requested initial quality.

Supplementary mode:

```text
--mode observation-noise
```

This mode perturbs `Feature.txt` image observations and leaves camera poses and 3D points unchanged. Use it for observation-noise robustness experiments, not as the main initialization-quality benchmark.

PVL-BA is used rather than COLMAP because it stores undistorted image observations and explicit BA variables. COLMAP observations are tied to camera distortion conventions, so perturbing them mixes measurement noise with distortion-model details.

## Recommended RMSE Levels

The default `main` preset generates:

```text
2 px, 5 px, 10 px, 20 px, 50 px, 100 px
```

Together with the original unperturbed dataset, this gives:

```text
original, 2 px, 5 px, 10 px, 20 px, 50 px, 100 px
```

The `stress` preset generates:

```text
200 px, 500 px
```

Use stress levels as a robustness supplement, not as the primary leaderboard. Each `noise_metadata.json` stores both pixel RMSE and normalized RMSE values.

## Usage

```powershell
python .\generate_noisy_pvl_ba.py `
  --input-dir ..\..\outputs\abs_at_pvl_ba_check `
  --output-root ..\..\outputs\pvl_ba_quality
```

This uses `--mode init-pose-triangulate` and the `main` RMSE preset.

The default pose-triangulation mode is streaming. Cameras and pose-noise vectors stay in memory, while `XYZ.txt` and `Feature.txt` are read point-by-point. The generated `Cam-*.txt` and `XYZ.txt` are rewritten, while static files such as `Feature.txt`, `cal.txt`, and GCP sidecars are hard-linked by default when the filesystem supports it.

Use custom levels:

```powershell
python .\generate_noisy_pvl_ba.py `
  --input-dir ..\..\outputs\abs_at_pvl_ba_check `
  --output-root ..\..\outputs\pvl_ba_quality_custom `
  --target-rmse 5 10 50 100
```

Generate stress levels:

```powershell
python .\generate_noisy_pvl_ba.py `
  --input-dir ..\..\outputs\abs_at_pvl_ba_check `
  --output-root ..\..\outputs\pvl_ba_quality_stress `
  --preset stress
```

Generate observation-noise variants:

```powershell
python .\generate_noisy_pvl_ba.py `
  --input-dir ..\..\outputs\abs_at_pvl_ba_check `
  --output-root ..\..\outputs\pvl_ba_observation_noise `
  --mode observation-noise `
  --target-rmse 5 10 50 100
```

Each output dataset includes `noise_metadata.json`.

For `init-pose-triangulate`, `noise_metadata.json` also records camera-center error, camera-rotation error, and tie-point coordinate error summaries relative to the input PVL-BA dataset. These metrics help explain high-RMSE stress levels: a few degrees of rotation error or a small center shift in world coordinates can become a large image-space residual after projection and re-triangulation.

## Scale Notes

`init-pose-triangulate` supports two scale solvers:

```text
--scale-solver sampled
--scale-solver exact
```

`sampled` is the default and is recommended for large releases. It estimates the pose-noise scale from a deterministic point sample, then checks the scale on the full dataset. If the full RMSE is outside `--full-refine-tolerance` (default `0.02`), it runs up to `--full-refine-iterations` full-dataset bisection passes (default `12`) before writing the final dataset, stopping early once the tolerance is met. Existing variants are considered complete only when `noise_metadata.json` reports an `actual_rmse_px` within this tolerance.

`exact` runs full-dataset bisection and gives the closest target RMSE, but it is much slower on large blocks.

Stress levels can expose non-monotonic pose-scale behavior: a small number of nearly degenerate re-triangulated tracks may create sharp RMSE spikes, so plain bisection can miss a target such as `500 px`. In that case, first diagnose the scale/RMSE curve, then pin a known-good scale:

```powershell
python .\generate_noisy_pvl_ba.py `
  --input-dir path\to\original `
  --output-root path\to\quality `
  --target-rmse 500 `
  --pose-scale-override 500=0.894986
```

The generated metadata records `pose_scale_override`, `target_relative_error`, and `target_within_tolerance`. Use `--full-search-steps` only for targeted investigations, because each full-search probe scans the complete `XYZ.txt` and `Feature.txt`.

Use `--static-file-policy hardlink` to reduce local disk usage for quality variants. Use `--static-file-policy copy` when every variant must be physically independent on disk.
