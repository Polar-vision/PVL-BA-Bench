# PVL-BA Quality Variants

Generate noisy PVL-BA datasets with controlled initial reprojection RMSE.

## Naming

Use explicit field tags instead of bare positional numbers:

```text
problem-i<images>-p<points>-o<observations>-g<gcps>
```

For initialization-quality variants, append the target initial RMSE:

```text
problem-i507-p40315-o139534-g3-init-rmse002p00px
problem-i507-p40315-o139534-g3-init-rmse005p00px
problem-i507-p40315-o139534-g3-init-rmse010p00px
problem-i507-p40315-o139534-g3-init-rmse020p00px
problem-i507-p40315-o139534-g3-init-rmse050p00px
problem-i507-p40315-o139534-g3-init-rmse100p00px
```

The tags make the name self-describing:

- `i`: valid network images.
- `p`: 3D tie points.
- `o`: tie-point image observations.
- `g`: GCP count.

## Why PVL-BA Noise

Noise variants should be generated in PVL-BA format rather than COLMAP format for this benchmark. PVL-BA stores undistorted image observations and explicit BA variables, so target initial RMSE levels are direct and comparable. COLMAP text models store distorted observations tied to camera distortion models, so adding pixel noise there mixes measurement noise with distortion-model conventions.

The tool perturbs `Feature.txt` image observations. Camera poses, 3D points, intrinsics, and GCP object coordinates are copied unchanged. GCP image observations are not perturbed by default.

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

Each output dataset includes `noise_metadata.json`.
