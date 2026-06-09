# PVL-BA Quality Variants

Generate PVL-BA quality variants with controlled initial reprojection RMSE.

## Naming

Use explicit field tags instead of bare positional numbers:

```text
problem-i<images>-p<points>-o<observations>-g<gcps>
```

`g` counts only GCPs with at least one measurement in the filtered BA image set.

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

Joint camera-pose and 3D point stress variants use `joint-rmse`:

```text
problem-i507-p40315-o139534-g3-joint-rmse200p00px
```

The tags make the name self-describing:

- `i`: valid network images.
- `p`: 3D tie points.
- `o`: tie-point image observations.
- `g`: GCP count.

## Modes

Default mode:

```text
--mode auto
```

This mode uses `init-pose-triangulate` for main levels and `init-pose-point` for stress levels.

Main initialization-quality mode:

```text
--mode init-pose-triangulate
```

This mode perturbs camera poses, keeps `Feature.txt` fixed, and re-triangulates `XYZ.txt` from the perturbed cameras and original image observations.

Stress initialization-quality mode:

```text
--mode init-pose-point
```

This mode perturbs camera poses and 3D tie-point coordinates directly, keeps `Feature.txt` fixed, and does not re-triangulate points. It is the default stress-test mode because it scales to very large blocks while still testing BA convergence from a poor joint initialization.

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

Use stress levels as a robustness supplement, not as the primary leaderboard. By default, stress levels are generated with `init-pose-point` and use the `joint-rmse` filename tag. Each `noise_metadata.json` stores both pixel RMSE and normalized RMSE values.

## Usage

```powershell
python .\generate_noisy_pvl_ba.py `
  --input-dir ..\..\outputs\abs_at_pvl_ba_check `
  --output-root ..\..\outputs\pvl_ba_quality
```

This uses `--mode auto` and the `main` RMSE preset.

Both initialization modes are streaming. Cameras and pose-noise vectors stay in memory, while `XYZ.txt` and `Feature.txt` are read point-by-point. `init-pose-triangulate` re-triangulates and rewrites `XYZ.txt`; `init-pose-point` directly perturbs and rewrites `XYZ.txt`. Static files such as `Feature.txt`, `cal.txt`, and GCP sidecars are hard-linked by default when the filesystem supports it.

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

This uses `init-pose-point` automatically. Pass `--mode init-pose-triangulate` only when you explicitly need the older re-triangulated stress behavior.

Generate observation-noise variants:

```powershell
python .\generate_noisy_pvl_ba.py `
  --input-dir ..\..\outputs\abs_at_pvl_ba_check `
  --output-root ..\..\outputs\pvl_ba_observation_noise `
  --mode observation-noise `
  --target-rmse 5 10 50 100
```

Each output dataset includes `noise_metadata.json`.

For initialization modes, `noise_metadata.json` also records camera-center error, camera-rotation error, and tie-point coordinate error summaries relative to the input PVL-BA dataset. These metrics help explain high-RMSE stress levels: a visually modest 3D initialization perturbation can still produce a large image-space residual after projection.

## Scale Notes

Initialization modes support two scale solvers:

```text
--scale-solver sampled
--scale-solver exact
```

`sampled` is the default and is recommended for large releases. It estimates the noise scale from a deterministic point sample, then checks the scale on the full dataset. If the full RMSE is outside `--full-refine-tolerance` (default `0.02`), it runs up to `--full-refine-iterations` full-dataset bisection passes (default `12`) before writing the final dataset, stopping early once the tolerance is met. Existing variants are considered complete only when `noise_metadata.json` reports an `actual_rmse_px` within this tolerance and the stored mode matches the requested mode.

`exact` runs full-dataset bisection and gives the closest target RMSE, but it is much slower on large blocks.

Re-triangulated stress levels can expose non-monotonic pose-scale behavior: a small number of nearly degenerate tracks may create sharp RMSE spikes, so plain bisection can miss a target such as `500 px`. If you explicitly use `--mode init-pose-triangulate` for stress, first diagnose the scale/RMSE curve, then pin a known-good scale:

```powershell
python .\generate_noisy_pvl_ba.py `
  --input-dir path\to\original `
  --output-root path\to\quality `
  --target-rmse 500 `
  --pose-scale-override 500=0.894986
```

The generated metadata records `pose_scale_override`, `target_relative_error`, and `target_within_tolerance`. Use `--full-search-steps` only for targeted investigations, because each full-search probe scans the complete `XYZ.txt` and `Feature.txt`.

Use `--static-file-policy hardlink` to reduce local disk usage for quality variants. Use `--static-file-policy copy` when every variant must be physically independent on disk.
