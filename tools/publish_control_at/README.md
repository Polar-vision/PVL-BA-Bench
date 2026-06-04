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
  -Viewers `
  -Dashboard `
  -DryRun
```

## Full Release

```powershell
powershell -ExecutionPolicy Bypass -File tools\publish_control_at\run.ps1 `
  -OutputRoot D:\PVL-BA-ControlAT `
  -Formats pvl-ba,colmap,bal `
  -Quality `
  -QualityPreset all `
  -Viewers `
  -Dashboard
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

## Resource Notes

The AT-to-PVL-BA, AT-to-COLMAP, and AT-to-BAL converters use streaming XML parsing and can process large BlocksExchange files without loading the complete XML tree into RAM.

Quality generation is a different workload. The default `init-pose-triangulate` mode repeatedly perturbs all cameras, re-triangulates all tie points, and evaluates all image observations to hit each target RMSE. For the 20-block control-AT manifest, the original PVL-BA/COLMAP/BAL text exports are expected to be on the order of 150 GB, while eight PVL-BA quality variants can add several hundred GB more. Run the full release on a large output disk and process very large blocks individually if memory or wall time is limited.
