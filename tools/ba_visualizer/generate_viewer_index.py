#!/usr/bin/env python3
"""Generate an HTML index for a directory of BA viewer HTML files."""

from __future__ import annotations

import argparse
import csv
import html
import json
import re
from dataclasses import dataclass
from pathlib import Path


DATASET_RE = re.compile(
    r"^(?P<area>.+)-problem-i(?P<images>\d+)-p(?P<points>\d+)-o(?P<observations>\d+)-g(?P<gcps>\d+)(?:-c(?P<checkpoints>\d+))?$"
)
AREA_LABELS = {
    "abs": "ABS",
    "ba-colmap": "BA COLMAP",
    "changji": "Changji",
    "feice-road": "Feice Road",
    "rel": "REL",
    "shiyan": "Shiyan",
}


@dataclass(frozen=True)
class ViewerEntry:
    area: str
    file: str
    images: int
    points: int
    observations: int
    gcps: int
    checkpoints: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--viewer-dir", required=True, type=Path, help="Directory containing dataset viewer HTML files")
    parser.add_argument("--output", required=True, type=Path, help="Output index HTML")
    parser.add_argument("--manifest", type=Path, help="Optional control-AT manifest for counts and ordering")
    parser.add_argument("--title", default="PVL-BA Control AT Viewers")
    return parser.parse_args()


def manifest_entries(path: Path) -> dict[str, ViewerEntry]:
    entries = {}
    if not path or not path.exists():
        return entries
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            name = row["dataset_name"]
            entries[f"{name}.html"] = ViewerEntry(
                area=row["area"],
                file=f"{name}.html",
                images=int(row["valid_images"]),
                points=int(row["points"]),
                observations=int(row["observations"]),
                gcps=int(row["gcps"]),
                checkpoints=int(row["checkpoints"]),
            )
    return entries


def parse_filename(path: Path) -> ViewerEntry | None:
    match = DATASET_RE.fullmatch(path.stem)
    if not match:
        return None
    return ViewerEntry(
        area=match.group("area"),
        file=path.name,
        images=int(match.group("images")),
        points=int(match.group("points")),
        observations=int(match.group("observations")),
        gcps=int(match.group("gcps")),
        checkpoints=int(match.group("checkpoints") or 0),
    )


def collect_entries(viewer_dir: Path, manifest: Path | None) -> list[ViewerEntry]:
    by_manifest = manifest_entries(manifest) if manifest else {}
    if by_manifest:
        entries = [
            entry
            for filename, entry in by_manifest.items()
            if (viewer_dir / filename).exists()
        ]
        area_order = {area: index for index, area in enumerate(AREA_LABELS)}
        entries.sort(key=lambda item: (area_order.get(item.area, 99), item.file))
        return entries

    entries = []
    for path in sorted(viewer_dir.glob("*.html")):
        if path.name == "index.html":
            continue
        entry = parse_filename(path)
        if entry is not None:
            entries.append(entry)
    area_order = {area: index for index, area in enumerate(AREA_LABELS)}
    entries.sort(key=lambda item: (area_order.get(item.area, 99), item.file))
    return entries


def js_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def build_html(entries: list[ViewerEntry], title: str) -> str:
    areas = sorted({entry.area for entry in entries}, key=lambda area: (AREA_LABELS.get(area, area), area))
    area_options = "\n".join(
        f'          <option value="{html.escape(area)}">{html.escape(AREA_LABELS.get(area, area))}</option>'
        for area in areas
    )
    area_labels_json = json.dumps({area: AREA_LABELS.get(area, area) for area in areas}, ensure_ascii=False)
    datasets_json = json.dumps([entry.__dict__ for entry in entries], ensure_ascii=False, separators=(",", ":"))
    escaped_title = html.escape(title)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escaped_title}</title>
  <style>
    :root {{ color-scheme: dark; --bg: #121417; --panel: #1a1e23; --panel-2: #20262c; --line: rgba(255,255,255,0.12); --line-strong: rgba(255,255,255,0.22); --text: #eef2f6; --muted: #9facb8; --accent: #4fb7a8; }}
    * {{ box-sizing: border-box; }}
    html, body {{ margin: 0; width: 100%; height: 100%; overflow: hidden; font-family: "Segoe UI", Arial, sans-serif; background: var(--bg); color: var(--text); }}
    body {{ display: grid; grid-template-columns: minmax(300px, 390px) minmax(0, 1fr); }}
    aside {{ min-width: 0; height: 100vh; display: grid; grid-template-rows: auto auto auto minmax(0, 1fr); border-right: 1px solid var(--line); background: var(--panel); }}
    header {{ padding: 14px 16px 12px; border-bottom: 1px solid var(--line); }}
    h1 {{ margin: 0 0 8px; font-size: 17px; line-height: 1.25; letter-spacing: 0; }}
    .meta {{ display: flex; gap: 8px; flex-wrap: wrap; color: var(--muted); font-size: 12px; }}
    .toolbar {{ display: grid; gap: 10px; padding: 12px 16px; border-bottom: 1px solid var(--line); }}
    input, select, button {{ font: inherit; color: var(--text); background: var(--panel-2); border: 1px solid var(--line); border-radius: 6px; }}
    input, select {{ width: 100%; height: 34px; padding: 0 10px; outline: none; }}
    input:focus, select:focus {{ border-color: var(--accent); }}
    .filters {{ display: grid; grid-template-columns: minmax(0, 1fr) 116px; gap: 8px; }}
    .actions {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }}
    button {{ height: 32px; cursor: pointer; }}
    button:hover {{ border-color: var(--line-strong); background: #263039; }}
    button.active {{ border-color: var(--accent); background: rgba(79,183,168,0.18); }}
    .summary {{ display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; padding: 12px 16px; border-bottom: 1px solid var(--line); }}
    .summary div {{ min-width: 0; padding: 8px; background: rgba(255,255,255,0.05); border-radius: 6px; }}
    .summary span {{ display: block; color: var(--muted); font-size: 11px; }}
    .summary strong {{ display: block; margin-top: 2px; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .list {{ min-height: 0; overflow: auto; padding: 8px; }}
    .group-title {{ position: sticky; top: 0; z-index: 1; padding: 8px 8px 6px; color: var(--muted); background: var(--panel); font-size: 11px; text-transform: uppercase; letter-spacing: 0; }}
    .dataset {{ width: 100%; height: auto; min-height: 58px; margin: 0 0 6px; padding: 8px 10px; display: grid; gap: 5px; text-align: left; border-radius: 6px; }}
    .dataset .name {{ font-size: 13px; line-height: 1.25; overflow-wrap: anywhere; }}
    .dataset .counts {{ display: flex; flex-wrap: wrap; gap: 6px; color: var(--muted); font-size: 11px; }}
    .pill {{ padding: 1px 6px; border: 1px solid var(--line); border-radius: 999px; }}
    main {{ min-width: 0; height: 100vh; display: grid; grid-template-rows: 44px minmax(0, 1fr); }}
    .viewer-bar {{ display: flex; align-items: center; gap: 10px; min-width: 0; padding: 0 12px; border-bottom: 1px solid var(--line); background: #15181c; }}
    .viewer-title {{ min-width: 0; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; color: var(--muted); }}
    .viewer-bar button {{ width: 78px; }}
    iframe {{ width: 100%; height: 100%; border: 0; background: #101215; display: block; }}
    .empty {{ padding: 16px; color: var(--muted); font-size: 13px; }}
    @media (max-width: 860px) {{ body {{ grid-template-columns: 1fr; grid-template-rows: 45vh 55vh; }} aside {{ height: 45vh; border-right: 0; border-bottom: 1px solid var(--line); }} main {{ height: 55vh; }} }}
  </style>
</head>
<body>
  <aside>
    <header>
      <h1>{escaped_title}</h1>
      <div class="meta"><span id="datasetCount"></span><span id="activeArea"></span></div>
    </header>
    <section class="toolbar">
      <div class="filters">
        <input id="search" type="search" placeholder="Search dataset">
        <select id="area">
          <option value="all">All areas</option>
{area_options}
        </select>
      </div>
      <div class="actions">
        <button id="copyLink" type="button">Copy link</button>
        <button id="openFile" type="button">Open file</button>
      </div>
    </section>
    <section class="summary">
      <div><span>Images</span><strong id="images">-</strong></div>
      <div><span>Points</span><strong id="points">-</strong></div>
      <div><span>GCPs</span><strong id="gcps">-</strong></div>
    </section>
    <nav class="list" id="list"></nav>
  </aside>
  <main>
    <div class="viewer-bar">
      <div class="viewer-title" id="viewerTitle"></div>
      <button id="reload" type="button">Reload</button>
    </div>
    <iframe id="viewer" title="Selected PVL-BA viewer"></iframe>
  </main>
  <script>
    const datasets = {datasets_json};
    const areaNames = {area_labels_json};
    const list = document.getElementById("list");
    const viewer = document.getElementById("viewer");
    const viewerTitle = document.getElementById("viewerTitle");
    const search = document.getElementById("search");
    const area = document.getElementById("area");
    const fmt = new Intl.NumberFormat("en-US");
    let selected = null;

    function fileBase(file) {{ return file.replace(/\\.html$/, ""); }}
    function matches(dataset) {{
      const q = search.value.trim().toLowerCase();
      return (area.value === "all" || dataset.area === area.value) && (!q || fileBase(dataset.file).toLowerCase().includes(q));
    }}
    function setSelected(dataset, pushHash = true) {{
      selected = dataset;
      viewer.src = dataset.file;
      viewerTitle.textContent = fileBase(dataset.file);
      document.getElementById("images").textContent = fmt.format(dataset.images);
      document.getElementById("points").textContent = fmt.format(dataset.points);
      document.getElementById("gcps").textContent = `${{fmt.format(dataset.gcps)}} / ${{fmt.format(dataset.checkpoints)}} check`;
      if (pushHash) history.replaceState(null, "", `#${{encodeURIComponent(fileBase(dataset.file))}}`);
      renderList();
    }}
    function renderList() {{
      const filtered = datasets.filter(matches);
      document.getElementById("datasetCount").textContent = `${{filtered.length}} of ${{datasets.length}} datasets`;
      document.getElementById("activeArea").textContent = area.value === "all" ? "All areas" : areaNames[area.value];
      list.innerHTML = "";
      if (filtered.length === 0) {{
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = "No matching datasets.";
        list.appendChild(empty);
        return;
      }}
      let currentArea = "";
      for (const dataset of filtered) {{
        if (dataset.area !== currentArea) {{
          currentArea = dataset.area;
          const title = document.createElement("div");
          title.className = "group-title";
          title.textContent = areaNames[currentArea] || currentArea;
          list.appendChild(title);
        }}
        const button = document.createElement("button");
        button.type = "button";
        button.className = `dataset ${{selected && selected.file === dataset.file ? "active" : ""}}`;
        const name = document.createElement("span");
        name.className = "name";
        name.textContent = fileBase(dataset.file);
        const counts = document.createElement("span");
        counts.className = "counts";
        for (const text of [`i ${{fmt.format(dataset.images)}}`, `p ${{fmt.format(dataset.points)}}`, `g ${{fmt.format(dataset.gcps)}}`]) {{
          const pill = document.createElement("span");
          pill.className = "pill";
          pill.textContent = text;
          counts.appendChild(pill);
        }}
        button.append(name, counts);
        button.addEventListener("click", () => setSelected(dataset));
        list.appendChild(button);
      }}
    }}

    search.addEventListener("input", renderList);
    area.addEventListener("change", renderList);
    document.getElementById("reload").addEventListener("click", () => {{ if (selected) viewer.src = selected.file; }});
    document.getElementById("openFile").addEventListener("click", () => {{ if (selected) window.open(selected.file, "_blank", "noopener"); }});
    document.getElementById("copyLink").addEventListener("click", async () => {{
      if (!selected) return;
      const link = new URL(selected.file, window.location.href).href;
      try {{ await navigator.clipboard.writeText(link); }} catch (_error) {{ window.prompt("Viewer link", link); }}
    }});

    const hashName = decodeURIComponent(window.location.hash.replace(/^#/, ""));
    const initial = datasets.find(dataset => fileBase(dataset.file) === hashName) || datasets[0];
    if (initial) setSelected(initial, false);
    if (hashName && initial) history.replaceState(null, "", `#${{encodeURIComponent(fileBase(initial.file))}}`);
  </script>
</body>
</html>
"""


def main() -> None:
    args = parse_args()
    entries = collect_entries(args.viewer_dir.resolve(), args.manifest.resolve() if args.manifest else None)
    if not entries:
        raise FileNotFoundError(f"No dataset viewer HTML files found in {args.viewer_dir}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_html(entries, args.title), encoding="utf-8", newline="\n")
    print(f"Wrote viewer index to {args.output.resolve()}")
    print(f"  viewers: {len(entries)}")


if __name__ == "__main__":
    main()
