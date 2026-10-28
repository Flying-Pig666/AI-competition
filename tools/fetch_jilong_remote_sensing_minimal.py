from __future__ import annotations

import csv
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import numpy as np
import planetary_computer
import rasterio
from pyproj import Transformer
from pystac import Item
from pystac_client import Client
from rasterio.enums import Resampling
from rasterio.merge import merge
from shapely.geometry import box, shape
from shapely.ops import unary_union

# Minimal AOI that includes the approximate Nepal-side source point and the Jilong event point.
BBOX_WGS84 = (85.25, 27.95, 86.05, 28.55)
AOI = box(*BBOX_WGS84)
EVENT_UTC = datetime(2026, 8, 26, 2, 30, tzinfo=timezone.utc)
OUT = Path("remote_sensing_minimal")
OUT.mkdir(parents=True, exist_ok=True)
TARGET_CRS = "EPSG:32645"
TARGET_RES = 30.0

CATALOG = Client.open(
    "https://planetarycomputer.microsoft.com/api/stac/v1",
    modifier=planetary_computer.sign_inplace,
)


def intersection_fraction(item: Item) -> float:
    try:
        return float(shape(item.geometry).intersection(AOI).area / AOI.area)
    except Exception:
        return 0.0


def group_by_date(items: Iterable[Item]) -> dict[str, list[Item]]:
    groups: dict[str, list[Item]] = defaultdict(list)
    for item in items:
        if item.datetime is not None:
            groups[item.datetime.date().isoformat()].append(item)
    return dict(groups)


def date_group_stats(group: list[Item]) -> dict:
    geoms = [shape(i.geometry).intersection(AOI) for i in group]
    coverage = float(unary_union(geoms).area / AOI.area) if geoms else 0.0
    weights = [max(g.area, 1e-12) for g in geoms]
    clouds = [float(i.properties.get("eo:cloud_cover", 100.0) or 100.0) for i in group]
    cloud = float(np.average(clouds, weights=weights)) if weights else 100.0
    dt = min(i.datetime for i in group if i.datetime is not None)
    return {"coverage": coverage, "cloud": cloud, "datetime": dt}


def choose_s2_group(items: list[Item], before: bool) -> tuple[str, list[Item], dict]:
    groups = group_by_date(items)
    candidates = []
    for date_key, group in groups.items():
        stats = date_group_stats(group)
        dt = stats["datetime"]
        if before and dt >= EVENT_UTC:
            continue
        if (not before) and dt < EVENT_UTC:
            continue
        days = abs((dt - EVENT_UTC).total_seconds()) / 86400.0
        # Favor near-complete AOI coverage first, then low cloud and temporal proximity.
        score = 1000.0 * min(stats["coverage"], 1.0) - 2.0 * stats["cloud"] - 1.0 * days
        candidates.append((score, date_key, group, stats))
    if not candidates:
        raise RuntimeError("No suitable Sentinel-2 group found")
    candidates.sort(key=lambda x: x[0], reverse=True)
    _, date_key, group, stats = candidates[0]
    return date_key, group, stats


def item_asset(item: Item, key: str):
    if key in item.assets:
        return item.assets[key]
    # Fallback aliases seen across STAC providers.
    aliases = {
        "B02": ["blue"],
        "B03": ["green"],
        "B04": ["red"],
        "B08": ["nir"],
        "B11": ["swir16"],
        "B12": ["swir22"],
        "SCL": ["scl"],
        "vv": ["VV"],
        "vh": ["VH"],
    }
    for alias in aliases.get(key, []):
        if alias in item.assets:
            return item.assets[alias]
    return None


def open_assets(items: list[Item], asset_key: str):
    datasets = []
    for item in items:
        asset = item_asset(item, asset_key)
        if asset is None:
            continue
        datasets.append(rasterio.open(asset.href))
    if not datasets:
        raise RuntimeError(f"No assets found for {asset_key}")
    return datasets


def write_s2_multiband(items: list[Item], output: Path) -> None:
    bands = [
        ("B02", "Blue_B02", Resampling.bilinear),
        ("B03", "Green_B03", Resampling.bilinear),
        ("B04", "Red_B04", Resampling.bilinear),
        ("B08", "NIR_B08", Resampling.bilinear),
        ("B11", "SWIR1_B11", Resampling.bilinear),
        ("B12", "SWIR2_B12", Resampling.bilinear),
        ("SCL", "Scene_Classification_SCL", Resampling.nearest),
    ]
    writer = None
    try:
        for idx, (key, desc, resampling) in enumerate(bands, start=1):
            srcs = open_assets(items, key)
            try:
                src_crs = srcs[0].crs
                transformer = Transformer.from_crs("EPSG:4326", src_crs, always_xy=True)
                left, bottom = transformer.transform(BBOX_WGS84[0], BBOX_WGS84[1])
                right, top = transformer.transform(BBOX_WGS84[2], BBOX_WGS84[3])
                arr, transform = merge(
                    srcs,
                    bounds=(left, bottom, right, top),
                    res=(TARGET_RES, TARGET_RES),
                    nodata=0,
                    dtype="uint16",
                    resampling=resampling,
                    method="first",
                )
                band = arr[0].astype(np.uint16, copy=False)
                if writer is None:
                    profile = {
                        "driver": "GTiff",
                        "height": band.shape[0],
                        "width": band.shape[1],
                        "count": len(bands),
                        "dtype": "uint16",
                        "crs": src_crs,
                        "transform": transform,
                        "nodata": 0,
                        "compress": "DEFLATE",
                        "predictor": 2,
                        "tiled": True,
                        "blockxsize": 512,
                        "blockysize": 512,
                        "BIGTIFF": "IF_SAFER",
                    }
                    writer = rasterio.open(output, "w", **profile)
                writer.write(band, idx)
                writer.set_band_description(idx, desc)
            finally:
                for src in srcs:
                    src.close()
    finally:
        if writer is not None:
            writer.update_tags(
                source="Microsoft Planetary Computer / Copernicus Sentinel-2 L2A",
                aoi_bbox_wgs84=",".join(map(str, BBOX_WGS84)),
                target_resolution_m=str(TARGET_RES),
            )
            writer.close()


def orbit_key(item: Item):
    return (
        item.properties.get("sat:orbit_state"),
        item.properties.get("sat:relative_orbit"),
    )


def choose_s1_pair(items: list[Item]) -> tuple[Item, Item] | None:
    valid = []
    for item in items:
        if item.datetime is None:
            continue
        if item_asset(item, "vv") is None or item_asset(item, "vh") is None:
            continue
        mode = item.properties.get("sar:instrument_mode")
        if mode and str(mode).upper() != "IW":
            continue
        valid.append(item)
    by_orbit: dict[tuple, list[Item]] = defaultdict(list)
    for item in valid:
        by_orbit[orbit_key(item)].append(item)
    candidates = []
    for key, group in by_orbit.items():
        pre = [i for i in group if i.datetime < EVENT_UTC]
        post = [i for i in group if i.datetime >= EVENT_UTC]
        if not pre or not post:
            continue
        p = max(pre, key=lambda i: i.datetime)
        q = min(post, key=lambda i: i.datetime)
        cov = min(intersection_fraction(p), intersection_fraction(q))
        days = ((EVENT_UTC - p.datetime).total_seconds() + (q.datetime - EVENT_UTC).total_seconds()) / 86400.0
        score = 1000.0 * cov - days
        candidates.append((score, p, q))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1], candidates[0][2]


def acquisition_group(all_items: list[Item], selected: Item) -> list[Item]:
    key = orbit_key(selected)
    return [
        i
        for i in all_items
        if i.datetime is not None
        and orbit_key(i) == key
        and abs((i.datetime - selected.datetime).total_seconds()) <= 300
        and item_asset(i, "vv") is not None
        and item_asset(i, "vh") is not None
    ]


def write_s1_multiband(items: list[Item], output: Path) -> None:
    bands = [("vv", "VV_dB"), ("vh", "VH_dB")]
    writer = None
    nodata = -9999.0
    try:
        for idx, (key, desc) in enumerate(bands, start=1):
            srcs = open_assets(items, key)
            try:
                src_crs = srcs[0].crs
                transformer = Transformer.from_crs("EPSG:4326", src_crs, always_xy=True)
                left, bottom = transformer.transform(BBOX_WGS84[0], BBOX_WGS84[1])
                right, top = transformer.transform(BBOX_WGS84[2], BBOX_WGS84[3])
                arr, transform = merge(
                    srcs,
                    bounds=(left, bottom, right, top),
                    res=(TARGET_RES, TARGET_RES),
                    nodata=0.0,
                    dtype="float32",
                    resampling=Resampling.bilinear,
                    method="first",
                )
                linear = arr[0].astype(np.float32, copy=False)
                out = np.full(linear.shape, nodata, dtype=np.float32)
                mask = np.isfinite(linear) & (linear > 0)
                out[mask] = 10.0 * np.log10(linear[mask])
                if writer is None:
                    profile = {
                        "driver": "GTiff",
                        "height": out.shape[0],
                        "width": out.shape[1],
                        "count": len(bands),
                        "dtype": "float32",
                        "crs": src_crs,
                        "transform": transform,
                        "nodata": nodata,
                        "compress": "DEFLATE",
                        "predictor": 3,
                        "tiled": True,
                        "blockxsize": 512,
                        "blockysize": 512,
                        "BIGTIFF": "IF_SAFER",
                    }
                    writer = rasterio.open(output, "w", **profile)
                writer.write(out, idx)
                writer.set_band_description(idx, desc)
            finally:
                for src in srcs:
                    src.close()
    finally:
        if writer is not None:
            writer.update_tags(
                source="Microsoft Planetary Computer / Sentinel-1 RTC",
                aoi_bbox_wgs84=",".join(map(str, BBOX_WGS84)),
                target_resolution_m=str(TARGET_RES),
                values="backscatter in dB",
            )
            writer.close()


def ids(items: list[Item]) -> str:
    return ";".join(i.id for i in items)


def main() -> None:
    manifest = []

    s2_search = CATALOG.search(
        collections=["sentinel-2-l2a"],
        bbox=BBOX_WGS84,
        datetime="2026-08-01T00:00:00Z/2026-09-20T23:59:59Z",
        query={"eo:cloud_cover": {"lt": 95}},
        max_items=500,
    )
    s2_items = list(s2_search.items())
    if not s2_items:
        raise RuntimeError("No Sentinel-2 L2A items found")

    pre_date, pre_group, pre_stats = choose_s2_group(s2_items, before=True)
    post_date, post_group, post_stats = choose_s2_group(s2_items, before=False)

    pre_s2 = OUT / f"Sentinel2_L2A_pre_{pre_date}_B02_B03_B04_B08_B11_B12_SCL_30m.tif"
    post_s2 = OUT / f"Sentinel2_L2A_post_{post_date}_B02_B03_B04_B08_B11_B12_SCL_30m.tif"
    write_s2_multiband(pre_group, pre_s2)
    write_s2_multiband(post_group, post_s2)
    manifest.extend([
        {
            "dataset": "Sentinel-2 L2A pre-event",
            "file": pre_s2.name,
            "acquisition": pre_date,
            "items": ids(pre_group),
            "coverage_fraction": f"{pre_stats['coverage']:.4f}",
            "cloud_cover": f"{pre_stats['cloud']:.2f}",
            "orbit": "",
            "bands": "B02,B03,B04,B08,B11,B12,SCL",
            "resolution_m": "30",
        },
        {
            "dataset": "Sentinel-2 L2A post-event",
            "file": post_s2.name,
            "acquisition": post_date,
            "items": ids(post_group),
            "coverage_fraction": f"{post_stats['coverage']:.4f}",
            "cloud_cover": f"{post_stats['cloud']:.2f}",
            "orbit": "",
            "bands": "B02,B03,B04,B08,B11,B12,SCL",
            "resolution_m": "30",
        },
    ])

    # Sentinel-1 RTC is added only if a same-orbit pre/post pair is available.
    s1_search = CATALOG.search(
        collections=["sentinel-1-rtc"],
        bbox=BBOX_WGS84,
        datetime="2026-07-01T00:00:00Z/2026-09-30T23:59:59Z",
        max_items=1000,
    )
    s1_items = list(s1_search.items())
    pair = choose_s1_pair(s1_items)
    if pair is not None:
        pre_item, post_item = pair
        pre_items = acquisition_group(s1_items, pre_item)
        post_items = acquisition_group(s1_items, post_item)
        pre_date_s1 = pre_item.datetime.strftime("%Y-%m-%dT%H%M%SZ")
        post_date_s1 = post_item.datetime.strftime("%Y-%m-%dT%H%M%SZ")
        pre_s1 = OUT / f"Sentinel1_RTC_pre_{pre_date_s1}_VV_VH_dB_30m.tif"
        post_s1 = OUT / f"Sentinel1_RTC_post_{post_date_s1}_VV_VH_dB_30m.tif"
        write_s1_multiband(pre_items, pre_s1)
        write_s1_multiband(post_items, post_s1)
        key = orbit_key(pre_item)
        manifest.extend([
            {
                "dataset": "Sentinel-1 RTC pre-event",
                "file": pre_s1.name,
                "acquisition": pre_item.datetime.isoformat(),
                "items": ids(pre_items),
                "coverage_fraction": f"{intersection_fraction(pre_item):.4f}",
                "cloud_cover": "",
                "orbit": f"{key[0]} / relative {key[1]}",
                "bands": "VV_dB,VH_dB",
                "resolution_m": "30",
            },
            {
                "dataset": "Sentinel-1 RTC post-event",
                "file": post_s1.name,
                "acquisition": post_item.datetime.isoformat(),
                "items": ids(post_items),
                "coverage_fraction": f"{intersection_fraction(post_item):.4f}",
                "cloud_cover": "",
                "orbit": f"{key[0]} / relative {key[1]}",
                "bands": "VV_dB,VH_dB",
                "resolution_m": "30",
            },
        ])

    manifest_path = OUT / "remote_sensing_manifest.csv"
    with manifest_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "dataset", "file", "acquisition", "items", "coverage_fraction",
                "cloud_cover", "orbit", "bands", "resolution_m",
            ],
        )
        writer.writeheader()
        writer.writerows(manifest)

    summary = {
        "aoi_bbox_wgs84": BBOX_WGS84,
        "event_utc": EVENT_UTC.isoformat(),
        "files": [p.name for p in OUT.iterdir() if p.is_file()],
        "manifest_rows": manifest,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
