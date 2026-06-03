# PVL-BA Quality Variants

Generate noisy PVL-BA datasets with controlled initial reprojection RMSE.

## Naming

Use explicit field tags instead of bare positional numbers:

```text
problem-i<images>-p<points>-o<observations>-g<gcps>
```

For noise-quality variants, append the target RMSE:

```text
problem-i507-p40315-o139534-g3-rmse005p00px
problem-i507-p40315-o139534-g3-rmse010p00px
problem-i507-p40315-o139534-g3-rmse050p00px
problem-i507-p40315-o139534-g3-rmse100p00px
```

The tags make the name self-describing:

- `i`: valid network images.
- `p`: 3D tie points.
- `o`: tie-point image observations.
- `g`: GCP count.

## Why PVL-BA Noise

Noise variants should be generated in PVL-BA format rather than COLMAP format for this benchmark. PVL-BA stores undistorted image observations and explicit BA variables, so target initial RMSE levels are direct and comparable. COLMAP text models store distorted observations tied to camera distortion models, so adding pixel noise there mixes measurement noise with distortion-model conventions.

The tool perturbs `Feature.txt` image observations. Camera poses, 3D points, intrinsics, and GCP object coordinates are copied unchanged. GCP image observations are not perturbed by default.

## Usage

```powershell
python .\generate_noisy_pvl_ba.py `
  --input-dir ..\..\outputs\abs_at_pvl_ba_check `
  --output-root ..\..\outputs\pvl_ba_quality `
  --target-rmse 5 10 50 100
```

Each output dataset includes `noise_metadata.json`.
