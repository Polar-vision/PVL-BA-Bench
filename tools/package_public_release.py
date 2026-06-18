#!/usr/bin/env python3
"""Prepare public release metadata, viewer indexes, and optional archives."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import os
import shutil
import sys
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Collection:
    key: str
    title: str
    manifest: Path
    release_root: Path


@dataclass(frozen=True)
class Artifact:
    collection: str
    dataset_name: str
    format_name: str
    source_path: Path
    relative_package_path: Path
    complete: bool
    size_bytes: int
    file_count: int


COLLECTIONS = {
    "abs": Collection(
        key="abs",
        title="ABS Cloud Earth blocks",
        manifest=ROOT / "manifests/abs_16.csv",
        release_root=ROOT / "outputs/abs_release",
    ),
    "rel": Collection(
        key="rel",
        title="REL Cloud Earth blocks",
        manifest=ROOT / "manifests/rel.csv",
        release_root=ROOT / "outputs/rel_release",
    ),
    "control_at_20": Collection(
        key="control_at_20",
        title="Controlled aerotriangulation blocks",
        manifest=ROOT / "manifests/control_at_20.csv",
        release_root=ROOT / "outputs/control_at_20_release",
    ),
    "ba_colmap": Collection(
        key="ba_colmap",
        title="COLMAP text-model BA blocks",
        manifest=ROOT / "manifests/ba_colmap_122.csv",
        release_root=ROOT / "outputs/ba_colmap_release",
    ),
}

FORMATS = ("pvl-ba", "colmap", "bal")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT / "outputs/public_release")
    parser.add_argument("--collections", nargs="+", choices=sorted(COLLECTIONS), default=sorted(COLLECTIONS))
    parser.add_argument("--formats", nargs="+", choices=FORMATS, default=list(FORMATS))
    parser.add_argument("--package", action="store_true", help="Create per-dataset archives.")
    parser.add_argument(
        "--archive-format",
        choices=("zip", "tar", "tar.gz"),
        default="zip",
        help="Archive format used with --package. zip is the most portable on Windows.",
    )
    parser.add_argument("--compression-level", type=int, default=1, help="Compression level for zip/tar.gz.")
    parser.add_argument("--skip-existing", action="store_true", help="Do not rebuild existing archives.")
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Fail if any requested dataset format or viewer is incomplete.",
    )
    parser.add_argument("--copy-viewers", action="store_true", help="Copy viewer HTML files into site/viewers.")
    parser.add_argument("--base-data-url", default="", help="Public URL prefix for packages/, e.g. https://host/data/")
    parser.add_argument("--base-viewer-url", default="", help="Public URL prefix for viewer HTML files.")
    parser.add_argument("--limit", type=int, default=0, help="Limit rows per collection for smoke tests.")
    return parser.parse_args()


def read_manifest(path: Path, limit: int = 0) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if limit:
        return rows[:limit]
    return rows


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def tree_stats(path: Path) -> tuple[int, int]:
    if path.is_file():
        return file_size(path), 1
    if not path.exists():
        return 0, 0
    size = 0
    count = 0
    for child in path.rglob("*"):
        if child.is_file():
            size += file_size(child)
            count += 1
    return size, count


def pvl_ba_complete(path: Path) -> bool:
    original = path / "original"
    return (
        (original / "cal.txt").exists()
        and (original / "Feature.txt").exists()
        and (original / "XYZ.txt").exists()
        and any(original.glob("Cam-*.txt"))
    )


def colmap_complete(path: Path) -> bool:
    original = path / "original"
    return (
        (original / "cameras.txt").exists()
        and (original / "images.txt").exists()
        and (original / "points3D.txt").exists()
    )


def bal_complete(path: Path) -> bool:
    bal_path = path / "original.bal"
    return bal_path.exists() and file_size(bal_path) > 0


def viewer_complete(collection: Collection, dataset_name: str) -> bool:
    return (collection.release_root / "viewers" / f"{dataset_name}.html").exists()


def artifact_for(collection: Collection, row: dict[str, str], format_name: str) -> Artifact:
    dataset_name = row["dataset_name"]
    source_path = collection.release_root / format_name / dataset_name
    if format_name == "pvl-ba":
        complete = pvl_ba_complete(source_path)
    elif format_name == "colmap":
        complete = colmap_complete(source_path)
    elif format_name == "bal":
        complete = bal_complete(source_path)
    else:
        raise ValueError(format_name)
    size, count = tree_stats(source_path)
    suffix = archive_suffix(format_name="zip")
    return Artifact(
        collection=collection.key,
        dataset_name=dataset_name,
        format_name=format_name,
        source_path=source_path,
        relative_package_path=Path("packages") / format_name / collection.key / f"{dataset_name}{suffix}",
        complete=complete,
        size_bytes=size,
        file_count=count,
    )


def archive_suffix(format_name: str) -> str:
    if format_name == "zip":
        return ".zip"
    if format_name == "tar":
        return ".tar"
    if format_name == "tar.gz":
        return ".tar.gz"
    raise ValueError(format_name)


def normalize_base_url(value: str) -> str:
    if not value:
        return ""
    return value.rstrip("/") + "/"


def url_join(base: str, relative: str) -> str:
    if not base:
        return relative.replace("\\", "/")
    return normalize_base_url(base) + relative.replace("\\", "/")


def iter_files_for_archive(source_path: Path) -> Iterable[Path]:
    if source_path.is_file():
        yield source_path
        return
    for path in sorted(source_path.rglob("*")):
        if path.is_file():
            yield path


def create_zip(source_path: Path, output_path: Path, compression_level: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    compression = zipfile.ZIP_DEFLATED if compression_level > 0 else zipfile.ZIP_STORED
    compresslevel = compression_level if compression == zipfile.ZIP_DEFLATED else None
    with zipfile.ZipFile(output_path, "w", compression=compression, compresslevel=compresslevel, allowZip64=True) as zf:
        root_name = source_path.name
        for file_path in iter_files_for_archive(source_path):
            arcname = Path(root_name) / file_path.relative_to(source_path)
            zf.write(file_path, arcname.as_posix())


def create_tar(source_path: Path, output_path: Path, archive_format: str, compression_level: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "w:gz" if archive_format == "tar.gz" else "w"
    kwargs = {"compresslevel": compression_level} if archive_format == "tar.gz" else {}
    with tarfile.open(output_path, mode, **kwargs) as tf:
        tf.add(source_path, arcname=source_path.name)


def create_archive(artifact: Artifact, output_path: Path, archive_format: str, compression_level: int) -> None:
    if archive_format == "zip":
        create_zip(artifact.source_path, output_path, compression_level)
    elif archive_format in ("tar", "tar.gz"):
        create_tar(artifact.source_path, output_path, archive_format, compression_level)
    else:
        raise ValueError(archive_format)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024 * 16) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def write_csv(path: Path, rows: list[dict[str, object]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def collect_rows(args: argparse.Namespace) -> tuple[list[dict[str, object]], list[Artifact], list[dict[str, object]]]:
    dataset_rows: list[dict[str, object]] = []
    artifacts: list[Artifact] = []
    viewer_rows: list[dict[str, object]] = []
    for key in args.collections:
        collection = COLLECTIONS[key]
        rows = read_manifest(collection.manifest, args.limit)
        for row in rows:
            dataset_name = row["dataset_name"]
            dataset_rows.append(
                {
                    "collection": key,
                    "dataset_name": dataset_name,
                    "area": row.get("area", ""),
                    "source_name": row.get("source_name", ""),
                    "images": row.get("valid_images", ""),
                    "points": row.get("points", ""),
                    "observations": row.get("observations", ""),
                    "gcps": row.get("gcps", ""),
                    "checkpoints": row.get("checkpoints", ""),
                    "gcp_observations": row.get("gcp_observations", ""),
                    "source_format": row.get("source_format", "BlocksExchange XML"),
                    "source_software": row.get("source_software", ""),
                }
            )
            for format_name in args.formats:
                artifact = artifact_for(collection, row, format_name)
                suffix = archive_suffix(args.archive_format)
                artifacts.append(
                    Artifact(
                        collection=artifact.collection,
                        dataset_name=artifact.dataset_name,
                        format_name=artifact.format_name,
                        source_path=artifact.source_path,
                        relative_package_path=artifact.relative_package_path.with_suffix(suffix),
                        complete=artifact.complete,
                        size_bytes=artifact.size_bytes,
                        file_count=artifact.file_count,
                    )
                )
            viewer_path = collection.release_root / "viewers" / f"{dataset_name}.html"
            size = file_size(viewer_path)
            viewer_rows.append(
                {
                    "collection": key,
                    "dataset_name": dataset_name,
                    "viewer_source": str(viewer_path),
                    "viewer_file": f"{dataset_name}.html",
                    "complete": viewer_path.exists(),
                    "size_bytes": size,
                }
            )
    return dataset_rows, artifacts, viewer_rows


def package_artifacts(args: argparse.Namespace, artifacts: list[Artifact]) -> list[dict[str, object]]:
    package_rows: list[dict[str, object]] = []
    for index, artifact in enumerate(artifacts, start=1):
        output_path = args.output_root / artifact.relative_package_path
        if not artifact.complete:
            package_rows.append(package_row(artifact, output_path, "", "incomplete"))
            print(f"[{index}/{len(artifacts)}] incomplete {artifact.collection}/{artifact.dataset_name}/{artifact.format_name}")
            continue
        if args.package:
            if args.skip_existing and output_path.exists() and output_path.stat().st_size > 0:
                print(f"[{index}/{len(artifacts)}] skip existing {output_path}")
            else:
                print(f"[{index}/{len(artifacts)}] package {artifact.collection}/{artifact.dataset_name}/{artifact.format_name}")
                create_archive(artifact, output_path, args.archive_format, args.compression_level)
            checksum = sha256_file(output_path) if output_path.exists() else ""
            status = "packaged" if output_path.exists() else "missing_package"
        else:
            checksum = ""
            status = "ready" if artifact.complete else "incomplete"
        package_rows.append(package_row(artifact, output_path, checksum, status))
    return package_rows


def package_row(artifact: Artifact, output_path: Path, checksum: str, status: str) -> dict[str, object]:
    return {
        "collection": artifact.collection,
        "dataset_name": artifact.dataset_name,
        "format": artifact.format_name,
        "status": status,
        "source_path": str(artifact.source_path),
        "package_path": str(output_path),
        "relative_package_path": artifact.relative_package_path.as_posix(),
        "size_bytes": artifact.size_bytes,
        "file_count": artifact.file_count,
        "sha256": checksum,
    }


def write_checksums(output_root: Path, package_rows: list[dict[str, object]]) -> None:
    lines = []
    for row in package_rows:
        checksum = str(row.get("sha256", ""))
        if checksum:
            lines.append(f"{checksum}  {row['relative_package_path']}")
    write_text(output_root / "manifest/checksums.sha256", "\n".join(lines) + ("\n" if lines else ""))


def copy_viewers(args: argparse.Namespace, viewer_rows: list[dict[str, object]]) -> None:
    if not args.copy_viewers:
        return
    viewer_root = args.output_root / "site/viewers"
    viewer_root.mkdir(parents=True, exist_ok=True)
    for row in viewer_rows:
        source = Path(str(row["viewer_source"]))
        if source.exists():
            shutil.copy2(source, viewer_root / str(row["viewer_file"]))


def write_site_index(
    output_root: Path,
    dataset_rows: list[dict[str, object]],
    package_rows: list[dict[str, object]],
    viewer_rows: list[dict[str, object]],
    base_data_url: str,
    base_viewer_url: str,
    copied_viewers: bool,
) -> None:
    package_by_dataset: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in package_rows:
        package_by_dataset.setdefault((str(row["collection"]), str(row["dataset_name"])), []).append(row)
    viewer_by_dataset = {(str(row["collection"]), str(row["dataset_name"])): row for row in viewer_rows}

    body_rows = []
    for dataset in dataset_rows:
        key = (str(dataset["collection"]), str(dataset["dataset_name"]))
        viewer = viewer_by_dataset.get(key)
        if viewer and viewer.get("complete"):
            viewer_file = str(viewer["viewer_file"])
            if copied_viewers:
                viewer_href = f"viewers/{viewer_file}"
            elif base_viewer_url:
                viewer_href = url_join(base_viewer_url, viewer_file)
            else:
                viewer_href = os.path.relpath(str(viewer["viewer_source"]), output_root / "site").replace("\\", "/")
            viewer_link = f'<a href="{html.escape(viewer_href)}">view</a>'
        else:
            viewer_link = '<span class="muted">missing</span>'
        download_links = []
        for artifact in package_by_dataset.get(key, []):
            label = str(artifact["format"])
            if artifact["status"] in ("ready", "packaged"):
                href = url_join(base_data_url, str(artifact["relative_package_path"]))
                download_links.append(f'<a href="{html.escape(href)}">{html.escape(label)}</a>')
            else:
                download_links.append(f'<span class="muted">{html.escape(label)}</span>')
        body_rows.append(
            "<tr>"
            f"<td>{html.escape(str(dataset['collection']))}</td>"
            f"<td>{html.escape(str(dataset['dataset_name']))}</td>"
            f"<td>{html.escape(str(dataset['images']))}</td>"
            f"<td>{html.escape(str(dataset['points']))}</td>"
            f"<td>{html.escape(str(dataset['observations']))}</td>"
            f"<td>{html.escape(str(dataset['gcps']))}</td>"
            f"<td>{viewer_link}</td>"
            f"<td>{' '.join(download_links)}</td>"
            "</tr>"
        )

    document = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PVL-BA-Bench Public Release</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 32px; color: #202124; }}
    table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
    th, td {{ border-bottom: 1px solid #ddd; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #f5f6f7; position: sticky; top: 0; }}
    a {{ color: #0b57d0; text-decoration: none; margin-right: 8px; }}
    .muted {{ color: #777; margin-right: 8px; }}
  </style>
</head>
<body>
  <h1>PVL-BA-Bench Public Release</h1>
  <p>This index links interactive viewers and downloadable PVL-BA, COLMAP, and BAL packages.</p>
  <table>
    <thead>
      <tr>
        <th>Collection</th>
        <th>Dataset</th>
        <th>Images</th>
        <th>Points</th>
        <th>Observations</th>
        <th>GCPs</th>
        <th>Viewer</th>
        <th>Downloads</th>
      </tr>
    </thead>
    <tbody>
      {''.join(body_rows)}
    </tbody>
  </table>
</body>
</html>
"""
    write_text(output_root / "site/index.html", document)


def write_url_list(output_root: Path, package_rows: list[dict[str, object]], viewer_rows: list[dict[str, object]], base_data_url: str, base_viewer_url: str, copied_viewers: bool) -> None:
    lines = []
    for row in package_rows:
        if row["status"] in ("ready", "packaged"):
            lines.append(url_join(base_data_url, str(row["relative_package_path"])))
    for row in viewer_rows:
        if row.get("complete"):
            viewer_file = str(row["viewer_file"])
            if copied_viewers:
                lines.append(f"site/viewers/{viewer_file}")
            elif base_viewer_url:
                lines.append(url_join(base_viewer_url, viewer_file))
            else:
                lines.append(os.path.relpath(str(row["viewer_source"]), output_root / "site").replace("\\", "/"))
    write_text(output_root / "manifest/urls.txt", "\n".join(lines) + ("\n" if lines else ""))


def fail_on_incomplete_artifacts(args: argparse.Namespace, artifacts: list[Artifact], viewer_rows: list[dict[str, object]]) -> None:
    if not args.require_complete:
        return
    incomplete_artifacts = [artifact for artifact in artifacts if not artifact.complete]
    incomplete_viewers = [row for row in viewer_rows if not row["complete"]]
    if incomplete_artifacts or incomplete_viewers:
        print("Incomplete release artifacts:", file=sys.stderr)
        for artifact in incomplete_artifacts[:50]:
            print(f"  {artifact.collection} {artifact.dataset_name} {artifact.format_name}", file=sys.stderr)
        for row in incomplete_viewers[:50]:
            print(f"  {row['collection']} {row['dataset_name']} viewer", file=sys.stderr)
        if len(incomplete_artifacts) + len(incomplete_viewers) > 100:
            print("  ...", file=sys.stderr)
        raise SystemExit(2)


def main() -> None:
    args = parse_args()
    args.output_root = args.output_root.resolve()
    args.base_data_url = normalize_base_url(args.base_data_url)
    args.base_viewer_url = normalize_base_url(args.base_viewer_url)

    dataset_rows, artifacts, viewer_rows = collect_rows(args)
    fail_on_incomplete_artifacts(args, artifacts, viewer_rows)
    package_rows = package_artifacts(args, artifacts)

    write_csv(
        args.output_root / "manifest/datasets.csv",
        dataset_rows,
        [
            "collection",
            "dataset_name",
            "area",
            "source_name",
            "images",
            "points",
            "observations",
            "gcps",
            "checkpoints",
            "gcp_observations",
            "source_format",
            "source_software",
        ],
    )
    write_json(args.output_root / "manifest/datasets.json", dataset_rows)
    write_csv(
        args.output_root / "manifest/artifacts.csv",
        package_rows,
        [
            "collection",
            "dataset_name",
            "format",
            "status",
            "source_path",
            "package_path",
            "relative_package_path",
            "size_bytes",
            "file_count",
            "sha256",
        ],
    )
    write_json(args.output_root / "manifest/artifacts.json", package_rows)
    write_csv(
        args.output_root / "manifest/viewers.csv",
        viewer_rows,
        ["collection", "dataset_name", "viewer_source", "viewer_file", "complete", "size_bytes"],
    )
    write_json(args.output_root / "manifest/viewers.json", viewer_rows)
    write_checksums(args.output_root, package_rows)
    copy_viewers(args, viewer_rows)
    write_site_index(
        args.output_root,
        dataset_rows,
        package_rows,
        viewer_rows,
        args.base_data_url,
        args.base_viewer_url,
        args.copy_viewers,
    )
    write_url_list(args.output_root, package_rows, viewer_rows, args.base_data_url, args.base_viewer_url, args.copy_viewers)

    incomplete = sum(1 for row in package_rows if row["status"] == "incomplete")
    viewer_missing = sum(1 for row in viewer_rows if not row["complete"])
    print(f"Wrote public release metadata to {args.output_root}")
    print(f"Datasets: {len(dataset_rows)}")
    print(f"Artifacts: {len(package_rows)} ({incomplete} incomplete)")
    print(f"Viewers: {len(viewer_rows)} ({viewer_missing} missing)")
    print(f"Site index: {args.output_root / 'site/index.html'}")


if __name__ == "__main__":
    main()
