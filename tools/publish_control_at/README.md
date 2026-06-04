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
