#!/usr/bin/env python3
"""Verify AT.xml rotation direction by sampling reprojection error."""

from __future__ import annotations

import argparse
import math
import xml.etree.ElementTree as ET
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Input BlocksExchange AT.xml")
    parser.add_argument("--samples", type=int, default=20000, help="Number of measurements to sample")
    return parser.parse_args()


def mv(matrix: list[list[float]], vector: list[float]) -> list[float]:
    return [sum(matrix[i][j] * vector[j] for j in range(3)) for i in range(3)]


def mtv(matrix: list[list[float]], vector: list[float]) -> list[float]:
    return [sum(matrix[j][i] * vector[j] for j in range(3)) for i in range(3)]


def percentile(values: list[float], ratio: float) -> float:
    return values[min(len(values) - 1, int(len(values) * ratio))]


def main() -> None:
    args = parse_args()
    root = ET.parse(args.input).getroot()
    photogroup = root.find(".//Photogroup")
    if photogroup is None:
        raise ValueError("Missing Photogroup")

    fx = float(photogroup.findtext("FocalLengthPixels"))
    fy = fx
    principal = photogroup.find("PrincipalPoint")
    distortion = photogroup.find("Distortion")
    if principal is None or distortion is None:
        raise ValueError("Missing PrincipalPoint or Distortion")
    cx = float(principal.findtext("x"))
    cy = float(principal.findtext("y"))
    k1 = float(distortion.findtext("K1"))
    k2 = float(distortion.findtext("K2"))
    k3 = float(distortion.findtext("K3"))
    p1 = float(distortion.findtext("P1"))
    p2 = float(distortion.findtext("P2"))

    photos = {}
    for photo in photogroup.findall("Photo"):
        photo_id = int(photo.findtext("Id"))
        rotation = photo.find("Pose/Rotation")
        center = photo.find("Pose/Center")
        if rotation is None or center is None:
            continue
        R = [[float(rotation.findtext(f"M_{i}{j}")) for j in range(3)] for i in range(3)]
        C = [float(center.findtext(axis)) for axis in ("x", "y", "z")]
        photos[photo_id] = (R, C)

    def project(point_camera: list[float]) -> tuple[float, float] | None:
        X, Y, Z = point_camera
        if abs(Z) < 1e-12:
            return None
        x = X / Z
        y = Y / Z
        r2 = x * x + y * y
        radial = 1.0 + k1 * r2 + k2 * r2 * r2 + k3 * r2 * r2 * r2
        xd = x * radial + 2.0 * p1 * x * y + p2 * (r2 + 2.0 * x * x)
        yd = y * radial + p1 * (r2 + 2.0 * y * y) + 2.0 * p2 * x * y
        return fx * xd + cx, fy * yd + cy

    stats = {name: [] for name in ("R_XminusC", "Rt_XminusC", "R_CminusX", "Rt_CminusX")}
    used = 0
    for tiepoint in root.findall(".//TiePoint"):
        position = tiepoint.find("Position")
        if position is None:
            continue
        Xw = [float(position.findtext(axis)) for axis in ("x", "y", "z")]
        for measurement in tiepoint.findall("Measurement"):
            photo_id = int(measurement.findtext("PhotoId"))
            observed = (float(measurement.findtext("x")), float(measurement.findtext("y")))
            R, C = photos[photo_id]
            x_minus_c = [Xw[i] - C[i] for i in range(3)]
            c_minus_x = [C[i] - Xw[i] for i in range(3)]
            variants = {
                "R_XminusC": mv(R, x_minus_c),
                "Rt_XminusC": mtv(R, x_minus_c),
                "R_CminusX": mv(R, c_minus_x),
                "Rt_CminusX": mtv(R, c_minus_x),
            }
            for name, point_camera in variants.items():
                projected = project(point_camera)
                if projected is None:
                    continue
                error = math.hypot(projected[0] - observed[0], projected[1] - observed[1])
                if math.isfinite(error) and error < 1e9:
                    stats[name].append(error)
            used += 1
            if used >= args.samples:
                break
        if used >= args.samples:
            break

    print(f"photos={len(photos)} sampled_measurements={used}")
    print("variant,n,mean,median,p95,max")
    for name, values in stats.items():
        values.sort()
        mean = sum(values) / len(values)
        print(
            f"{name},{len(values)},{mean:.6f},{values[len(values)//2]:.6f},"
            f"{percentile(values, 0.95):.6f},{values[-1]:.6f}"
        )


if __name__ == "__main__":
    main()
