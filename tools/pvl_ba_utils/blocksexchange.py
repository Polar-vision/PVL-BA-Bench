from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class SpatialReference:
    srs_id: int
    name: str
    definition: str


@dataclass(frozen=True)
class GcpObservation:
    image_index: int
    x: float
    y: float


@dataclass(frozen=True)
class GroundControlPoint:
    index: int
    name: str
    xyz: tuple[float, float, float]
    horizontal_accuracy: float
    vertical_accuracy: float
    is_check_point: bool
    source_srs_id: int
    source_xyz: tuple[float, float, float]
    category: str
    point_type: str
    observations: list[GcpObservation]


def text(parent: ET.Element, name: str) -> str:
    value = parent.findtext(name)
    if value is None:
        raise ValueError(f"Missing XML element {name}")
    return value


def optional_float(parent: ET.Element, name: str, default: float = float("nan")) -> float:
    value = parent.findtext(name)
    return default if value is None else float(value)


def ftext(parent: ET.Element, name: str) -> float:
    return float(text(parent, name))


def has_complete_pose(photo: ET.Element) -> bool:
    pose = photo.find("Pose")
    return pose is not None and pose.find("Rotation") is not None and pose.find("Center") is not None


def parse_spatial_references(root: ET.Element) -> dict[int, SpatialReference]:
    references: dict[int, SpatialReference] = {}
    for srs in root.findall("SpatialReferenceSystems/SRS"):
        srs_id = int(text(srs, "Id"))
        references[srs_id] = SpatialReference(
            srs_id=srs_id,
            name=srs.findtext("Name", ""),
            definition=srs.findtext("Definition", ""),
        )
    return references


def parse_enu_definition(definition: str) -> tuple[float, float, float]:
    match = re.fullmatch(r"\s*ENU:([^,]+),([^,]+)(?:,([^,]+))?\s*", definition)
    if not match:
        raise ValueError(f"Unsupported ENU definition: {definition}")
    lat0 = float(match.group(1))
    lon0 = float(match.group(2))
    h0 = float(match.group(3)) if match.group(3) is not None else 0.0
    return lat0, lon0, h0


def geodetic_to_ecef(lon_deg: float, lat_deg: float, height: float) -> tuple[float, float, float]:
    a = 6378137.0
    f = 1.0 / 298.257223563
    e2 = f * (2.0 - f)
    lon = math.radians(lon_deg)
    lat = math.radians(lat_deg)
    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    n = a / math.sqrt(1.0 - e2 * sin_lat * sin_lat)
    x = (n + height) * cos_lat * math.cos(lon)
    y = (n + height) * cos_lat * math.sin(lon)
    z = (n * (1.0 - e2) + height) * sin_lat
    return x, y, z


def ecef_to_enu(
    xyz: tuple[float, float, float],
    origin_xyz: tuple[float, float, float],
    origin_lon_deg: float,
    origin_lat_deg: float,
) -> tuple[float, float, float]:
    lon = math.radians(origin_lon_deg)
    lat = math.radians(origin_lat_deg)
    dx = xyz[0] - origin_xyz[0]
    dy = xyz[1] - origin_xyz[1]
    dz = xyz[2] - origin_xyz[2]
    sin_lon = math.sin(lon)
    cos_lon = math.cos(lon)
    sin_lat = math.sin(lat)
    cos_lat = math.cos(lat)
    east = -sin_lon * dx + cos_lon * dy
    north = -sin_lat * cos_lon * dx - sin_lat * sin_lon * dy + cos_lat * dz
    up = cos_lat * cos_lon * dx + cos_lat * sin_lon * dy + sin_lat * dz
    return east, north, up


def transform_to_target_srs(
    xyz: tuple[float, float, float],
    source_srs_id: int,
    target_srs_id: int,
    references: dict[int, SpatialReference],
) -> tuple[float, float, float]:
    if source_srs_id == target_srs_id:
        return xyz
    if source_srs_id not in references or target_srs_id not in references:
        raise ValueError(f"Cannot transform SRS {source_srs_id} to {target_srs_id}: missing SRS definition")

    source = references[source_srs_id].definition
    target = references[target_srs_id].definition
    if not target.startswith("ENU:"):
        raise ValueError(f"Unsupported target SRS for GCP transform: {target}")
    if not source.upper().startswith("EPSG:"):
        raise ValueError(f"Unsupported source SRS for GCP transform: {source}")

    try:
        from pyproj import Transformer
    except ImportError as exc:
        raise RuntimeError("pyproj is required to transform GCP coordinates between SRS definitions") from exc

    lon, lat, height = Transformer.from_crs(source.upper(), "EPSG:4979", always_xy=True).transform(*xyz)
    lat0, lon0, h0 = parse_enu_definition(target)
    origin = geodetic_to_ecef(lon0, lat0, h0)
    point = geodetic_to_ecef(lon, lat, height)
    return ecef_to_enu(point, origin, lon0, lat0)


def parse_ground_control_points(
    block: ET.Element,
    photo_index_by_source_id: dict[int, int],
    references: dict[int, SpatialReference],
    target_srs_id: int,
    observation_transform: Callable[[int, float, float], tuple[float, float]],
) -> list[GroundControlPoint]:
    control_points = block.find("ControlPoints")
    if control_points is None:
        return []

    gcps: list[GroundControlPoint] = []
    for control_point in control_points.findall("ControlPoint"):
        gcps.append(
            control_point_to_gcp(
                control_point,
                len(gcps),
                photo_index_by_source_id,
                references,
                target_srs_id,
                observation_transform,
            )
        )
    return gcps


def control_point_to_gcp(
    control_point: ET.Element,
    index: int,
    photo_index_by_source_id: dict[int, int],
    references: dict[int, SpatialReference],
    target_srs_id: int,
    observation_transform: Callable[[int, float, float], tuple[float, float]],
) -> GroundControlPoint:
    source_srs_id = int(text(control_point, "SRSId"))
    position = control_point.find("Position")
    if position is None:
        raise ValueError("ControlPoint without Position")
    source_xyz = tuple(ftext(position, axis) for axis in ("x", "y", "z"))
    xyz = transform_to_target_srs(source_xyz, source_srs_id, target_srs_id, references)
    observations: list[GcpObservation] = []
    for measurement in control_point.findall("Measurement"):
        source_photo_id = int(text(measurement, "PhotoId"))
        image_index = photo_index_by_source_id.get(source_photo_id)
        if image_index is None:
            continue
        x, y = observation_transform(source_photo_id, ftext(measurement, "x"), ftext(measurement, "y"))
        observations.append(GcpObservation(image_index, x, y))
    observations.sort(key=lambda observation: (observation.image_index, observation.x, observation.y))
    return GroundControlPoint(
        index=index,
        name=control_point.findtext("Name", f"gcp_{index}"),
        xyz=xyz,
        horizontal_accuracy=optional_float(control_point, "HorizontalAccuracy"),
        vertical_accuracy=optional_float(control_point, "VerticalAccuracy"),
        is_check_point=control_point.findtext("CheckPoint", "false").strip().lower() == "true",
        source_srs_id=source_srs_id,
        source_xyz=source_xyz,
        category=control_point.findtext("Category", ""),
        point_type=control_point.findtext("PointType", ""),
        observations=observations,
    )


def remove_from_parent(stack: list[ET.Element], element: ET.Element) -> None:
    if len(stack) > 1:
        parent = stack[-2]
        try:
            parent.remove(element)
        except ValueError:
            pass
    element.clear()


def stream_ground_control_points(
    input_path: Path,
    photo_index_by_source_id: dict[int, int],
    references: dict[int, SpatialReference],
    target_srs_id: int,
    observation_transform: Callable[[int, float, float], tuple[float, float]],
) -> list[GroundControlPoint]:
    gcps: list[GroundControlPoint] = []
    stack: list[ET.Element] = []
    for event, element in ET.iterparse(input_path, events=("start", "end")):
        if event == "start":
            stack.append(element)
            continue
        if element.tag == "ControlPoint":
            gcps.append(
                control_point_to_gcp(
                    element,
                    len(gcps),
                    photo_index_by_source_id,
                    references,
                    target_srs_id,
                    observation_transform,
                )
            )
            remove_from_parent(stack, element)
        elif element.tag in {"SRS", "Photogroup", "TiePoint"}:
            remove_from_parent(stack, element)
        stack.pop()
    return gcps


def write_gcp_files(metadata_path: Path, observations_path: Path, gcps: list[GroundControlPoint]) -> None:
    with metadata_path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# GCP_ID NAME X Y Z H_ACC V_ACC IS_CHECK_POINT SOURCE_SRS_ID SOURCE_X SOURCE_Y SOURCE_Z CATEGORY POINT_TYPE\n")
        for gcp in gcps:
            fields = [
                str(gcp.index),
                gcp.name,
                f"{gcp.xyz[0]:.12f}",
                f"{gcp.xyz[1]:.12f}",
                f"{gcp.xyz[2]:.12f}",
                f"{gcp.horizontal_accuracy:.12g}",
                f"{gcp.vertical_accuracy:.12g}",
                "1" if gcp.is_check_point else "0",
                str(gcp.source_srs_id),
                f"{gcp.source_xyz[0]:.12f}",
                f"{gcp.source_xyz[1]:.12f}",
                f"{gcp.source_xyz[2]:.12f}",
                gcp.category,
                gcp.point_type,
            ]
            fh.write(" ".join(fields).rstrip())
            fh.write("\n")

    with observations_path.open("w", encoding="utf-8", newline="\n") as fh:
        fh.write("# GCP_ID NUM_OBSERVATIONS IMAGE_INDEX X Y ...\n")
        for gcp in gcps:
            fields = [str(gcp.index), str(len(gcp.observations))]
            for observation in gcp.observations:
                fields.extend((str(observation.image_index), f"{observation.x:.12f}", f"{observation.y:.12f}"))
            fh.write(" ".join(fields))
            fh.write("\n")
