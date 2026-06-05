# Publish Control-AT Blocks

Batch-publish the 20 unique controlled aerotriangulation blocks listed in:

```text
manifests/control_at_20.csv
```

The manifest is deduplicated by BA problem statistics. The three Shiyan first-batch XML exports have identical image, tie-point, observation, GCP, checkpoint, and GCP-observation counts; only one is listed for release.

## Dry Run

Always start with a dry run:

```powershell
powershell -ExecutionPolicy Bypass -File tools\publish_control_at\run.ps1 `
  -OutputRoot D:\PVL-BA-ControlAT `
  -Formats pvl-ba,colmap,bal `
  -Quality `
  -QualityPreset all `
  -ScaleSolver sampled `
  -StaticFilePolicy hardlink `
  -Viewers `
  -ViewerCameraStride auto `
  -Dashboard `
  -SkipExisting `
  -DryRun
```

## Full Release

```powershell
powershell -ExecutionPolicy Bypass -File tools\publish_control_at\run.ps1 `
  -OutputRoot D:\PVL-BA-ControlAT `
  -Formats pvl-ba,colmap,bal `
  -Quality `
  -QualityPreset all `
  -ScaleSolver sampled `
  -StaticFilePolicy hardlink `
  -Viewers `
  -ViewerCameraStride auto `
  -Dashboard `
  -SkipExisting
```

By default, the PowerShell wrapper forwards `--viewer-camera-stride auto`, which adapts from the manifest image count and targets about 5,000 embedded frustums per quality level. For example, a 35,193-image block uses stride 8. To override it explicitly:

```powershell
powershell -ExecutionPolicy Bypass -File tools\publish_control_at\run.ps1 `
  -OutputRoot D:\PVL-BA-ControlAT `
  -Formats pvl-ba `
  -Viewers `
  -ViewerCameraStride 10
```

The release tree uses:

```text
pvl-ba/<dataset>/original
pvl-ba/<dataset>/quality/<dataset>-init-rmse002p00px
colmap/<dataset>/original
bal/<dataset>/original.bal
viewers/<dataset>.html
dashboard.html
```

Quality variants are generated from PVL-BA using the default `init-pose-triangulate` mode:

```text
original
2, 5, 10, 20, 50, 100 px
200, 500 px
```

The linked viewer groups `2..100 px` as the main benchmark and `200, 500 px` as stress tests.

`-SkipExisting` makes the release resumable. It skips PVL-BA/COLMAP/BAL originals, quality variants, and viewers that already look complete. Quality variants are skipped only when their files exist and `noise_metadata.json` reports `actual_rmse_px` within `--full-refine-tolerance` of the requested target.

For large releases, `publish_control_at.py` forwards the streaming quality-generator options:

```text
--scale-solver sampled
--sample-max-points 50000
--full-refine-iterations 12
--full-refine-tolerance 0.02
--full-search-steps 0
--static-file-policy hardlink
```

Use `--scale-solver exact` only for smaller blocks where tighter target matching is more important than wall time. Full refine stops early once the full-dataset RMSE is within tolerance. Stress levels may be non-monotonic because degenerate re-triangulated tracks can create sharp RMSE spikes; inspect those cases and rerun the affected target with `--pose-scale-override TARGET=SCALE` from `tools/pvl_ba_quality/generate_noisy_pvl_ba.py`. Keep `--rotation-weight-deg` and `--translation-weight` fixed across a release so `Pose scale` remains comparable between quality levels.

## Resource Notes

The AT-to-PVL-BA, AT-to-COLMAP, and AT-to-BAL converters use streaming XML parsing and can process large BlocksExchange files without loading the complete XML tree into RAM.

Quality generation is a different workload. The default `init-pose-triangulate` mode now streams `XYZ.txt` and `Feature.txt`, but it still repeatedly perturbs all cameras, re-triangulates tie points, and evaluates image observations to hit each target RMSE. For the 20-block control-AT manifest, the original PVL-BA/COLMAP/BAL text exports are expected to be on the order of 150 GB, while eight PVL-BA quality variants can add several hundred GB more. Run the full release on a large output disk and process very large blocks individually if wall time is limited.
