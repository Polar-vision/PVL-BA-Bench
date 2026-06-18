#!/usr/bin/env python
"""Write a readable progress snapshot for a Hugging Face large-folder upload."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
REPORT_RE = re.compile(r"^-+\s+([0-9-]+\s+[0-9:]+)\s+\(([^)]*)\)\s+-+")
FILES_RE = re.compile(
    r"Files:\s+hashed\s+(\d+)/(\d+)\s+\(([0-9.]+)G/([0-9.]+)G\)\s+\|\s+"
    r"pre-uploaded:\s+(\d+)/(\d+)\s+\(([0-9.]+)G/([0-9.]+)G\)\s+\|\s+"
    r"committed:\s+(\d+)/(\d+)\s+\(([0-9.]+)G/([0-9.]+)G\)\s+\|\s+"
    r"ignored:\s+(\d+)"
)
WORKERS_RE = re.compile(r"Workers:\s+(.+)")
WORKER_ITEM_RE = re.compile(r"([^:|]+):\s*(\d+)")


@dataclass
class CliSnapshot:
    report_time: datetime
    elapsed_text: str
    hash_done: int
    hash_total: int
    hash_gb: float
    total_gb: float
    pre_done: int
    pre_total: int
    pre_gb: float
    committed_done: int
    committed_total: int
    committed_gb: float
    ignored: int
    workers: dict[str, int]


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text).replace("\r", "")


def pct(done: float, total: float) -> str:
    if total <= 0:
        return "n/a"
    return f"{done * 100.0 / total:.1f}%"


def hours_text(hours: float | None) -> str:
    if hours is None or hours <= 0:
        return "n/a"
    if hours < 1:
        return f"{hours * 60:.0f} min"
    if hours < 48:
        return f"{hours:.1f} h"
    return f"{hours / 24:.1f} d"


def parse_cli_snapshots(log_path: Path) -> list[CliSnapshot]:
    if not log_path.exists():
        return []

    lines = strip_ansi(log_path.read_text(encoding="utf-8", errors="ignore")).splitlines()
    snapshots: list[CliSnapshot] = []
    current_time: datetime | None = None
    current_elapsed = ""
    pending: dict[str, object] | None = None

    for line in lines:
        report = REPORT_RE.match(line.strip())
        if report:
            try:
                current_time = datetime.strptime(report.group(1), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                current_time = None
            current_elapsed = report.group(2)
            pending = None
            continue

        files = FILES_RE.search(line)
        if files and current_time is not None:
            pending = {
                "report_time": current_time,
                "elapsed_text": current_elapsed,
                "hash_done": int(files.group(1)),
                "hash_total": int(files.group(2)),
                "hash_gb": float(files.group(3)),
                "total_gb": float(files.group(4)),
                "pre_done": int(files.group(5)),
                "pre_total": int(files.group(6)),
                "pre_gb": float(files.group(7)),
                "committed_done": int(files.group(9)),
                "committed_total": int(files.group(10)),
                "committed_gb": float(files.group(11)),
                "ignored": int(files.group(13)),
            }
            continue

        workers = WORKERS_RE.search(line)
        if workers and pending is not None:
            parsed_workers = {
                key.strip(): int(value)
                for key, value in WORKER_ITEM_RE.findall(workers.group(1))
            }
            snapshots.append(CliSnapshot(workers=parsed_workers, **pending))
            pending = None

    return snapshots


def latest_raw_monitor(raw_monitor_path: Path) -> dict[str, str]:
    if not raw_monitor_path.exists():
        return {}

    blocks: list[list[str]] = []
    current: list[str] = []
    for line in raw_monitor_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.startswith("[") and "running=" in line:
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append(current)
    if not blocks:
        return {}

    out: dict[str, str] = {}
    for line in blocks[-1]:
        if line.startswith("["):
            out["sample_line"] = line.strip()
            ts_match = re.match(r"\[([^]]+)\]", line)
            if ts_match:
                out["sample_time"] = ts_match.group(1)
            delta_match = re.search(r"wlan_sent_delta_mb=([0-9.]+)", line)
            if delta_match:
                out["wlan_sent_delta_mb"] = delta_match.group(1)
            continue

        match = re.search(r"([A-Za-z0-9_]+)=([^ ]+.*)$", line.strip())
        if match:
            out[match.group(1)] = match.group(2).strip()
    return out


def xet_log_stats(xet_log_path: Path) -> dict[str, str]:
    if not xet_log_path.exists():
        return {}

    clean_files: set[str] = set()
    clean_gb = 0.0
    inserted_xorbs: dict[str, int] = {}
    warnings = 0
    errors = 0
    last_ts = ""

    with xet_log_path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = obj.get("timestamp")
            if ts:
                last_ts = ts

            level = obj.get("level")
            if level == "WARN":
                warnings += 1
            elif level == "ERROR":
                errors += 1

            fields = obj.get("fields") or {}
            if fields.get("action") == "clean":
                file_name = fields.get("file_name", "")
                if file_name and file_name not in clean_files:
                    clean_files.add(file_name)
                    clean_gb += int(fields.get("file_size_count") or 0) / 1e9

            if (
                fields.get("message") == "Completed upload_xorb API call"
                and fields.get("result") == "inserted"
            ):
                xorb_hash = fields.get("hash", "")
                if xorb_hash and xorb_hash not in inserted_xorbs:
                    inserted_xorbs[xorb_hash] = int(fields.get("size") or 0)

    age = "n/a"
    if last_ts:
        try:
            parsed = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            age = f"{max((datetime.now(parsed.tzinfo) - parsed).total_seconds(), 0):.0f}"
        except ValueError:
            age = "n/a"

    return {
        "clean_files": str(len(clean_files)),
        "clean_gb": f"{clean_gb:.2f}",
        "unique_xorbs": str(len(inserted_xorbs)),
        "unique_xorb_gb": f"{sum(inserted_xorbs.values()) / 1e9:.2f}",
        "warnings": str(warnings),
        "errors": str(errors),
        "xet_log_age_sec": age,
    }


def query_remote(repo_id: str, repo_type: str) -> dict[str, object]:
    from huggingface_hub import HfApi

    files = HfApi().list_repo_files(repo_id=repo_id, repo_type=repo_type)
    package_zips = [f for f in files if f.startswith("packages/") and f.endswith(".zip")]
    by_format = Counter()
    by_collection = Counter()
    dataset_formats: dict[tuple[str, str], set[str]] = defaultdict(set)
    for path in package_zips:
        parts = path.split("/")
        if len(parts) < 4:
            continue
        fmt, collection = parts[1], parts[2]
        dataset = Path(parts[-1]).stem
        by_format[fmt] += 1
        by_collection[collection] += 1
        dataset_formats[(collection, dataset)].add(fmt)

    required = {"pvl-ba", "colmap", "bal"}
    complete_datasets = sum(1 for formats in dataset_formats.values() if required <= formats)
    return {
        "files": files,
        "remote_total": len(files),
        "remote_zip": len(package_zips),
        "remote_manifest": len([f for f in files if f.startswith("manifest/")]),
        "remote_site": len([f for f in files if f.startswith("site/")]),
        "by_format": by_format,
        "by_collection": by_collection,
        "complete_datasets": complete_datasets,
    }


def process_running(pids: Iterable[int]) -> bool:
    active = False
    for pid in pids:
        if os.name == "nt":
            # PROCESS_QUERY_LIMITED_INFORMATION. This avoids false negatives from
            # os.kill(pid, 0) on Windows while keeping the check read-only.
            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                active = True
            continue
        try:
            os.kill(pid, 0)
        except OSError:
            continue
        active = True
    return active


def build_snapshot(args: argparse.Namespace) -> str:
    cli_snapshots = parse_cli_snapshots(Path(args.upload_log))
    latest = cli_snapshots[-1] if cli_snapshots else None
    raw = latest_raw_monitor(Path(args.raw_monitor_log)) if args.raw_monitor_log else {}
    if args.xet_log:
        raw.update(xet_log_stats(Path(args.xet_log)))

    remote: dict[str, object] = {}
    remote_error = ""
    try:
        remote = query_remote(args.repo_id, args.repo_type)
    except Exception as exc:  # Keep monitor non-fatal during network hiccups.
        remote_error = f"{type(exc).__name__}: {exc}"

    now = datetime.now()
    lines = [
        "=" * 78,
        f"time: {now:%Y-%m-%d %H:%M:%S}",
        f"repo: {args.repo_id}",
    ]

    if latest is None:
        lines.append("status: upload log not parsed yet")
        return "\n".join(lines) + "\n"

    remaining_gb = max(latest.total_gb - latest.committed_gb, 0.0)
    avg_rate = None
    recent_rate = None
    if latest.committed_gb > 0 and cli_snapshots:
        first = cli_snapshots[0]
        elapsed_h = max((latest.report_time - first.report_time).total_seconds() / 3600.0, 0.001)
        avg_rate = latest.committed_gb / elapsed_h

        recent = [s for s in cli_snapshots if (latest.report_time - s.report_time).total_seconds() <= 3600]
        if len(recent) >= 2:
            dt_h = max((latest.report_time - recent[0].report_time).total_seconds() / 3600.0, 0.001)
            delta_gb = latest.committed_gb - recent[0].committed_gb
            if delta_gb > 0:
                recent_rate = delta_gb / dt_h

    eta_recent = remaining_gb / recent_rate if recent_rate else None
    eta_avg = remaining_gb / avg_rate if avg_rate else None

    remote_zip = int(remote.get("remote_zip", 0)) if remote else 0
    remote_manifest = int(remote.get("remote_manifest", 0)) if remote else 0
    remote_site = int(remote.get("remote_site", 0)) if remote else 0
    complete_datasets = int(remote.get("complete_datasets", 0)) if remote else 0

    lines.extend(
        [
            "stage: uploading package contents and committing visible repo files",
            f"process: {'running' if process_running(args.pids) else 'not detected'}",
            "",
            "phase progress:",
            f"  1 hash/checksum: {latest.hash_done}/{latest.hash_total} files, "
            f"{latest.hash_gb:.1f}/{latest.total_gb:.1f} GB ({pct(latest.hash_gb, latest.total_gb)})",
            f"  2 pre-upload to HF storage: {latest.pre_done}/{latest.pre_total} files "
            f"({pct(latest.pre_done, latest.pre_total)}), {latest.pre_gb:.1f}/{latest.total_gb:.1f} GB "
            f"({pct(latest.pre_gb, latest.total_gb)})",
            f"  3 commit to dataset repo: {latest.committed_done}/{latest.committed_total} files "
            f"({pct(latest.committed_done, latest.committed_total)}), {latest.committed_gb:.1f}/{latest.total_gb:.1f} GB "
            f"({pct(latest.committed_gb, latest.total_gb)})",
            f"  4 remote visible/downloadable: packages={remote_zip}/2934 ({pct(remote_zip, 2934)}), "
            f"manifest={remote_manifest}/8, site={remote_site}/1",
            f"  5 datasets complete with pvl-ba+colmap+bal: {complete_datasets}/978 ({pct(complete_datasets, 978)})",
            "",
            "worker status:",
            "  "
            + ", ".join(f"{key}={value}" for key, value in sorted(latest.workers.items()))
            if latest.workers
            else "  n/a",
            "",
            "xet/raw monitor:",
            f"  clean_files={raw.get('clean_files', 'n/a')}, clean_gb={raw.get('clean_gb', 'n/a')}, "
            f"unique_xorbs={raw.get('unique_xorbs', 'n/a')}, unique_xorb_gb={raw.get('unique_xorb_gb', 'n/a')}",
            f"  warnings={raw.get('warnings', 'n/a')}, errors={raw.get('errors', 'n/a')}, "
            f"last_wlan_sent_delta_mb={raw.get('wlan_sent_delta_mb', 'n/a')}, "
            f"xet_log_age_sec={raw.get('xet_log_age_sec', 'n/a')}",
            "",
            "rough ETA:",
            f"  recent committed rate: {recent_rate:.2f} GB/h, eta={hours_text(eta_recent)}"
            if recent_rate
            else "  recent committed rate: n/a",
            f"  average committed rate: {avg_rate:.2f} GB/h, eta={hours_text(eta_avg)}"
            if avg_rate
            else "  average committed rate: n/a",
        ]
    )

    if remote:
        by_format: Counter[str] = remote["by_format"]  # type: ignore[assignment]
        by_collection: Counter[str] = remote["by_collection"]  # type: ignore[assignment]
        lines.extend(
            [
                "",
                "remote packages by format:",
                "  " + ", ".join(f"{key}={by_format[key]}" for key in sorted(by_format))
                if by_format
                else "  none",
                "remote packages by collection:",
                "  " + ", ".join(f"{key}={by_collection[key]}" for key in sorted(by_collection))
                if by_collection
                else "  none",
            ]
        )

    if remote_error:
        lines.append(f"remote query error: {remote_error}")

    lines.append(
        "read this as: hash is done; pre-upload/commit are still running; "
        "remote visible files are the ones the community can already download."
    )
    return "\n".join(lines) + "\n"


def parse_pids(value: str) -> list[int]:
    if not value:
        return []
    return [int(part) for part in re.split(r"[,; ]+", value.strip()) if part]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-id", default="Polar-vision/PVL-BA-Bench")
    parser.add_argument("--repo-type", default="dataset")
    parser.add_argument("--upload-log", required=True)
    parser.add_argument("--raw-monitor-log", default="")
    parser.add_argument("--xet-log", default="")
    parser.add_argument("--output-log", required=True)
    parser.add_argument("--interval", type=int, default=0)
    parser.add_argument("--pids", default="")
    parser.add_argument("--max-samples", type=int, default=0)
    args = parser.parse_args()
    args.pids = parse_pids(args.pids)

    output_log = Path(args.output_log)
    output_log.parent.mkdir(parents=True, exist_ok=True)

    sample = 0
    while True:
        sample += 1
        snapshot = build_snapshot(args)
        with output_log.open("a", encoding="utf-8") as fh:
            fh.write(snapshot)

        if args.interval <= 0:
            break
        if args.max_samples and sample >= args.max_samples:
            break
        if args.pids and not process_running(args.pids):
            break
        time.sleep(args.interval)

    return 0


if __name__ == "__main__":
    sys.exit(main())
