#!/usr/bin/env python3
"""Fetch and package the public external datasets used for the Jilong debris-flow study.

The script intentionally excludes all official contest attachments. It downloads only the
external meteorological/reanalysis/forecast products that were introduced in the modelling
workflow, writes source metadata and query URLs, and produces a reproducible ZIP archive.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import shutil
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

ROOT = Path("external_data_used")
OUT_ZIP = Path("jilong_external_data_used_20261025.zip")
TZ = "Asia/Shanghai"
USER_AGENT = "MathorCup-Jilong-research/1.0 (public-data-retrieval)"

EVENT = {"name": "Jilong_event_point", "lat": 28.30, "lon": 85.40}
SOURCE = {"name": "Nepal_near_source", "lat": 27.98, "lon": 85.95}
NASA_POINT = {"name": "NASA_POWER_validation_point", "lat": 28.40, "lon": 85.45}


def request_json(base_url: str, params: dict[str, Any], retries: int = 5) -> tuple[dict[str, Any], str]:
    query = urllib.parse.urlencode(params, doseq=True, safe=",/:+")
    url = f"{base_url}?{query}"
    last_exc: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                raw = resp.read()
            obj = json.loads(raw.decode("utf-8"))
            if isinstance(obj, dict) and obj.get("error"):
                raise RuntimeError(f"API error: {obj}")
            return obj, url
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            time.sleep(2 ** attempt)
    raise RuntimeError(f"Failed after {retries} attempts: {url}\n{last_exc}")


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        seen: list[str] = []
        for row in rows:
            for key in row:
                if key not in seen:
                    seen.append(key)
        fieldnames = seen
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def openmeteo_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    hourly = payload.get("hourly") or {}
    times = hourly.get("time") or []
    keys = [k for k in hourly.keys() if k != "time"]
    rows: list[dict[str, Any]] = []
    for i, t in enumerate(times):
        row: dict[str, Any] = {"time": t}
        for key in keys:
            values = hourly.get(key) or []
            row[key] = values[i] if i < len(values) else None
        rows.append(row)
    return rows


def fetch_era5(point: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    variables = [
        "temperature_2m",
        "relative_humidity_2m",
        "dew_point_2m",
        "precipitation",
        "rain",
        "snowfall",
        "surface_pressure",
        "wind_speed_10m",
        "wind_direction_10m",
        "wind_gusts_10m",
    ]
    params = {
        "latitude": point["lat"],
        "longitude": point["lon"],
        "start_date": "2026-08-20",
        "end_date": "2026-08-27",
        "hourly": ",".join(variables),
        "timezone": TZ,
        "models": "era5",
    }
    payload, url = request_json("https://archive-api.open-meteo.com/v1/archive", params)
    rows = openmeteo_rows(payload)
    stem = f"era5_hourly_20260820_20260827_{point['name']}"
    write_csv(out_dir / f"{stem}.csv", rows)
    write_json(out_dir / f"{stem}_metadata.json", {
        "source": "Open-Meteo Historical Weather API (ERA5)",
        "query_url": url,
        "requested_point": point,
        "returned_latitude": payload.get("latitude"),
        "returned_longitude": payload.get("longitude"),
        "elevation_m": payload.get("elevation"),
        "timezone": payload.get("timezone"),
        "utc_offset_seconds": payload.get("utc_offset_seconds"),
        "hourly_units": payload.get("hourly_units"),
        "retrieved_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    })
    return {"dataset": stem, "rows": len(rows), "url": url, "status": "ok"}


def previous_runs_variables(base_vars: Iterable[str], days: range = range(1, 8)) -> list[str]:
    return [f"{v}_previous_day{d}" for v in base_vars for d in days]


def fetch_previous_runs(point: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    base_vars = ["temperature_2m", "precipitation", "wind_speed_10m", "surface_pressure"]
    variables = previous_runs_variables(base_vars)
    params = {
        "latitude": point["lat"],
        "longitude": point["lon"],
        "start_date": "2026-08-26",
        "end_date": "2026-08-26",
        "hourly": ",".join(variables),
        "timezone": TZ,
        "models": "ecmwf_ifs025",
    }
    payload, url = request_json("https://previous-runs-api.open-meteo.com/v1/forecast", params)
    rows_full = openmeteo_rows(payload)
    rows_08_18 = []
    for row in rows_full:
        try:
            hour = int(str(row["time"])[11:13])
        except Exception:  # noqa: BLE001
            continue
        if 8 <= hour <= 18:
            rows_08_18.append(row)
    stem = f"ecmwf_ifs_previous_runs_d1_d7_20260826_{point['name']}"
    write_csv(out_dir / f"{stem}_full_day.csv", rows_full)
    write_csv(out_dir / f"{stem}_08_18.csv", rows_08_18)
    write_json(out_dir / f"{stem}_metadata.json", {
        "source": "Open-Meteo Previous Model Runs API (ECMWF IFS 0.25 degree)",
        "query_url": url,
        "requested_point": point,
        "interpretation": "previous_day1=forecast made 24 h before valid time; ...; previous_day7=168 h before valid time",
        "returned_latitude": payload.get("latitude"),
        "returned_longitude": payload.get("longitude"),
        "elevation_m": payload.get("elevation"),
        "timezone": payload.get("timezone"),
        "hourly_units": payload.get("hourly_units"),
        "retrieved_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    })
    return {"dataset": stem, "rows_full": len(rows_full), "rows_08_18": len(rows_08_18), "url": url, "status": "ok"}


def fetch_era5_land_soil(point: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    variables = [
        "soil_temperature_0_to_7cm",
        "soil_temperature_7_to_28cm",
        "soil_temperature_28_to_100cm",
        "soil_temperature_100_to_255cm",
        "soil_moisture_0_to_7cm",
        "soil_moisture_7_to_28cm",
        "soil_moisture_28_to_100cm",
        "soil_moisture_100_to_255cm",
    ]
    params = {
        "latitude": point["lat"],
        "longitude": point["lon"],
        "start_date": "2026-08-20",
        "end_date": "2026-08-26",
        "hourly": ",".join(variables),
        "timezone": TZ,
        "models": "era5_land",
    }
    payload, url = request_json("https://archive-api.open-meteo.com/v1/archive", params)
    rows = openmeteo_rows(payload)
    stem = f"era5_land_soil_hourly_20260820_20260826_{point['name']}"
    write_csv(out_dir / f"{stem}.csv", rows)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row["time"])[:10]].append(row)
    daily_rows: list[dict[str, Any]] = []
    for date, vals in sorted(groups.items()):
        out: dict[str, Any] = {"date": date, "n_hours": len(vals)}
        for var in variables:
            nums = [float(r[var]) for r in vals if r.get(var) is not None and not (isinstance(r.get(var), float) and math.isnan(r[var]))]
            out[f"{var}_mean"] = sum(nums) / len(nums) if nums else None
            out[f"{var}_min"] = min(nums) if nums else None
            out[f"{var}_max"] = max(nums) if nums else None
        daily_rows.append(out)
    write_csv(out_dir / f"era5_land_soil_daily_summary_20260820_20260826_{point['name']}.csv", daily_rows)
    write_json(out_dir / f"{stem}_metadata.json", {
        "source": "Open-Meteo Historical Weather API (ERA5-Land)",
        "query_url": url,
        "requested_point": point,
        "returned_latitude": payload.get("latitude"),
        "returned_longitude": payload.get("longitude"),
        "elevation_m": payload.get("elevation"),
        "timezone": payload.get("timezone"),
        "hourly_units": payload.get("hourly_units"),
        "daily_summary": "Arithmetic mean/min/max of valid hourly values; not a separate source product.",
        "retrieved_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    })
    return {"dataset": stem, "rows_hourly": len(rows), "rows_daily": len(daily_rows), "url": url, "status": "ok"}


def fetch_nasa_power(point: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    params = {
        "parameters": "T2M,RH2M,PRECTOTCORR,WS10M,PS",
        "community": "AG",
        "longitude": point["lon"],
        "latitude": point["lat"],
        "start": "20260820",
        "end": "20260827",
        "format": "JSON",
        "time-standard": "UTC",
    }
    payload, url = request_json("https://power.larc.nasa.gov/api/temporal/hourly/point", params)
    parameter = (((payload.get("properties") or {}).get("parameter")) or {})
    timestamps: set[str] = set()
    for values in parameter.values():
        if isinstance(values, dict):
            timestamps.update(values.keys())
    rows: list[dict[str, Any]] = []
    for ts in sorted(timestamps):
        try:
            dt_utc = datetime.strptime(ts, "%Y%m%d%H")
            dt_bjt = dt_utc + timedelta(hours=8)
            local = dt_bjt.strftime("%Y-%m-%dT%H:%M")
        except Exception:  # noqa: BLE001
            local = ""
        row: dict[str, Any] = {"time_utc_yyyymmddhh": ts, "time_Asia_Shanghai": local}
        for var, values in parameter.items():
            row[var] = values.get(ts) if isinstance(values, dict) else None
        rows.append(row)
    stem = f"nasa_power_hourly_20260820_20260827_{point['name']}"
    write_csv(out_dir / f"{stem}.csv", rows)
    write_json(out_dir / f"{stem}_raw.json", payload)
    write_json(out_dir / f"{stem}_metadata.json", {
        "source": "NASA POWER Hourly Point API",
        "query_url": url,
        "requested_point": point,
        "time_standard_source": "UTC",
        "added_column": "time_Asia_Shanghai = UTC + 8 h",
        "parameter_units": (((payload.get("parameters") or {}))),
        "retrieved_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
    })
    return {"dataset": stem, "rows": len(rows), "url": url, "status": "ok"}


def try_fetch_ecmwf_ensemble(point: dict[str, Any], out_dir: Path) -> dict[str, Any]:
    """Try to reconstruct the optional ensemble-mean/spread table from a historical IFS run.

    This is retained only if the public Single Runs endpoint returns a valid payload. Failure
    is recorded, rather than substituting synthetic values.
    """
    variables = ["temperature_2m", "precipitation", "wind_speed_10m", "surface_pressure"]
    attempts: list[dict[str, Any]] = []
    for run in ["2026-08-25T00:00", "2026-08-24T00:00"]:
        params = {
            "latitude": point["lat"],
            "longitude": point["lon"],
            "run": run,
            "hourly": ",".join(variables),
            "models": "ecmwf_ifs025_ensemble_mean",
            "spread": "true",
            "timezone": TZ,
            "forecast_days": 10,
        }
        try:
            payload, url = request_json("https://single-runs-api.open-meteo.com/v1/forecast", params, retries=2)
            rows = openmeteo_rows(payload)
            target = [r for r in rows if str(r.get("time", "")).startswith("2026-08-26T") and 8 <= int(str(r["time"])[11:13]) <= 18]
            if target:
                stem = f"ecmwf_ifs_ensemble_mean_spread_run_{run[:10].replace('-', '')}_{point['name']}_valid_20260826_08_18"
                write_csv(out_dir / f"{stem}.csv", target)
                write_json(out_dir / f"{stem}_metadata.json", {
                    "source": "Open-Meteo Single Runs API, ECMWF IFS ensemble mean with spread",
                    "query_url": url,
                    "requested_point": point,
                    "model_run": run,
                    "returned_latitude": payload.get("latitude"),
                    "returned_longitude": payload.get("longitude"),
                    "hourly_units": payload.get("hourly_units"),
                    "retrieved_utc": datetime.utcnow().isoformat(timespec="seconds") + "Z",
                })
                return {"dataset": stem, "rows": len(target), "url": url, "status": "ok"}
            attempts.append({"run": run, "status": "no target rows", "url": url})
        except Exception as exc:  # noqa: BLE001
            attempts.append({"run": run, "status": "failed", "error": str(exc)})
    write_json(out_dir / f"ecmwf_ensemble_reconstruction_status_{point['name']}.json", {
        "status": "not included as numeric input",
        "reason": "The public historical ensemble endpoint did not return a valid target-period table. No synthetic replacement was generated.",
        "attempts": attempts,
    })
    return {"dataset": f"ecmwf_ensemble_{point['name']}", "status": "unavailable", "attempts": attempts}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    if ROOT.exists():
        shutil.rmtree(ROOT)
    ROOT.mkdir(parents=True)
    results: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    tasks = [
        ("01_ERA5_reanalysis", lambda d: fetch_era5(EVENT, d)),
        ("01_ERA5_reanalysis", lambda d: fetch_era5(SOURCE, d)),
        ("02_ECMWF_IFS_previous_runs", lambda d: fetch_previous_runs(EVENT, d)),
        ("02_ECMWF_IFS_previous_runs", lambda d: fetch_previous_runs(SOURCE, d)),
        ("03_ERA5_Land_soil", lambda d: fetch_era5_land_soil(EVENT, d)),
        ("04_NASA_POWER_validation", lambda d: fetch_nasa_power(NASA_POINT, d)),
        ("02_ECMWF_IFS_previous_runs", lambda d: try_fetch_ecmwf_ensemble(EVENT, d)),
        ("02_ECMWF_IFS_previous_runs", lambda d: try_fetch_ecmwf_ensemble(SOURCE, d)),
    ]
    for folder, func in tasks:
        try:
            result = func(ROOT / folder)
            results.append(result)
            print("OK", result.get("dataset"), result.get("status"))
        except Exception as exc:  # noqa: BLE001
            item = {"folder": folder, "status": "failed", "error": str(exc)}
            errors.append(item)
            print("ERROR", item, file=sys.stderr)

    readme = f"""# 吉隆泥石流研究：已使用外部数据整理包

生成时间（UTC）：{datetime.utcnow().isoformat(timespec='seconds')}Z

## 范围
本包仅收录建模流程中已经采用或明确用于交叉核验的外部公开数据；不重复收录赛题官方附件。

## 数据目录
1. `01_ERA5_reanalysis/`：吉隆口岸附近和尼泊尔近源区的 ERA5 小时再分析，2026-08-20—2026-08-27。
2. `02_ECMWF_IFS_previous_runs/`：ECMWF IFS 在有效时刻 2026-08-26 的提前 1—7 天固定提前量预报；含全天表和 08:00—18:00 表。
3. `03_ERA5_Land_soil/`：吉隆事件点 ERA5-Land 多层土壤温度、体积含水量小时表及日汇总，2026-08-20—2026-08-26。
4. `04_NASA_POWER_validation/`：NASA POWER 小时点数据，用于独立交叉核验；源时间为 UTC，并新增北京时间列。
5. `05_manifest_and_reproducibility/`：数据清单、来源 URL、检索结果、SHA-256 校验值和本重建脚本。

## 使用原则
- ERA5/ERA5-Land 是格点再分析，不能表述为地面站实测。
- ECMWF `previous_day1` 至 `previous_day7` 是固定提前量预报情景，不能直接当作 7 个独立样本或 95% 置信区间。
- NASA POWER 只作独立核验，不与 ERA5 简单平均。
- 若历史 ECMWF 集合均值/离散度无法从公开接口可靠重建，本包只保留失败状态说明，不生成合成或估算数据。
- 事件时刻 10:30 与小时资料不在同一分辨率；小时资料支持的时间约束是 `[10:00, 11:00)`。

## 许可与署名
- Open-Meteo API data: CC BY 4.0，使用时注明 Open-Meteo 及底层模型来源。
- NASA POWER: 使用时注明 NASA POWER Project / NASA Langley Research Center。

## 任务状态
成功：{len(results)} 项；失败：{len(errors)} 项。详细信息见 `retrieval_results.json`。
"""
    (ROOT / "README.md").write_text(readme, encoding="utf-8")
    repro_dir = ROOT / "05_manifest_and_reproducibility"
    repro_dir.mkdir(parents=True, exist_ok=True)
    write_json(repro_dir / "retrieval_results.json", {"results": results, "errors": errors})
    shutil.copy2(Path(__file__), repro_dir / Path(__file__).name)

    files = sorted(p for p in ROOT.rglob("*") if p.is_file())
    checksum_rows = [{"relative_path": str(p.relative_to(ROOT)), "size_bytes": p.stat().st_size, "sha256": sha256_file(p)} for p in files]
    write_csv(repro_dir / "sha256_checksums.csv", checksum_rows)

    # Recompute list after checksum file exists, then package everything.
    if OUT_ZIP.exists():
        OUT_ZIP.unlink()
    with zipfile.ZipFile(OUT_ZIP, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in sorted(ROOT.rglob("*")):
            if p.is_file():
                zf.write(p, arcname=str(Path(ROOT.name) / p.relative_to(ROOT)))

    print(f"Created {OUT_ZIP} ({OUT_ZIP.stat().st_size} bytes)")
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())
