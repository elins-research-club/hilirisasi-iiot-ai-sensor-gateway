from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

CANONICAL_FIELDS = (
    "temperature_c",
    "humidity_pct",
    "pressure_hpa",
    "bme_gas_ohm",
    "co_ppm",
    "no2_raw_mv",
    "no2_ratio",
    "o3_ppm",
    "co2_ppm",
    "pm1_ug_m3",
    "pm25_ug_m3",
    "pm10_ug_m3",
    "battery_voltage",
    "current_ma",
    "power_mw",
)


@dataclass(frozen=True)
class ZenodoPmReferenceStats:
    source: str
    output: str
    total_rows: int
    written_rows: int
    rejected_rows: int
    timezone_assumption: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    number = float(text)
    if number < 0:
        return None
    return number


def _timestamp(value: str, timezone_name: str) -> str:
    text = value.strip()
    if not text:
        raise ValueError("empty timestamp")
    parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
    if timezone_name != "UTC":
        from zoneinfo import ZoneInfo

        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name))
    else:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.isoformat()


def adapt_zenodo_pm_reference_csv(
    input_csv: str | Path,
    output_jsonl: str | Path,
    *,
    timezone_name: str = "UTC",
) -> ZenodoPmReferenceStats:
    """Adapt Zenodo record 7198378 Fidas 200S reference PM data.

    The source columns are reference-grade measurements. They intentionally stay
    under ``reference`` and are not copied into the project PMS7003T sensor lane.
    This prevents a public reference instrument from being misrepresented as a
    chip-identical project sensor.
    """

    source = Path(input_csv)
    output = Path(output_jsonl)
    output.parent.mkdir(parents=True, exist_ok=True)
    total = written = rejected = 0
    with source.open(encoding="utf-8-sig", newline="") as input_file, output.open(
        "w", encoding="utf-8"
    ) as output_file:
        reader = csv.DictReader(input_file)
        required = {"PM1", "PM2.5", "PM10", "date"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f"Zenodo PM CSV is missing required columns: {sorted(required)}")
        for row_number, row in enumerate(reader, start=2):
            if not any((value or "").strip() for value in row.values()):
                continue
            total += 1
            try:
                timestamp = _timestamp(row["date"], timezone_name)
                reference = {
                    "instrument": "Fidas 200S",
                    "pm1_ug_m3": _number(row.get("PM1")),
                    "pm25_ug_m3": _number(row.get("PM2.5")),
                    "pm10_ug_m3": _number(row.get("PM10")),
                    "pm_total_ug_m3": _number(row.get("PMtot")),
                }
            except (KeyError, TypeError, ValueError, OverflowError):
                rejected += 1
                continue
            if all(reference[name] is None for name in ("pm1_ug_m3", "pm25_ug_m3", "pm10_ug_m3")):
                rejected += 1
                continue
            sensor = {name: None for name in CANONICAL_FIELDS}
            record = {
                "schema_version": "iiot.dataset_record.v1",
                "dataset_id": "zenodo_fidas_pm_reference_7198378",
                "lane": "pm_reference_calibration",
                "record_id": f"zenodo-7198378-pm-{row_number}",
                "timestamp": timestamp,
                "device_id": "fidas_200s_reference",
                "site_id": "source_study_site",
                "sensor": sensor,
                "reference": reference,
                "quality": {
                    "valid": True,
                    "unit_conversion_applied": False,
                    "notes": [
                        "Reference-grade Fidas 200S values remain in reference.*.",
                        "Project PMS7003T sensor fields remain null until a paired low-cost record is joined.",
                        "Use time/site-aware calibration evaluation; do not claim chip-identical performance.",
                    ],
                },
                "provenance": {
                    "source_file": str(source),
                    "source_row": row_number,
                    "source_doi": "10.5281/zenodo.7198378",
                    "source_record": "df_pm_2min.csv",
                    "timezone_assumption": timezone_name,
                    "license": "CC BY 4.0",
                },
            }
            output_file.write(json.dumps(record, separators=(",", ":")) + "\n")
            written += 1
    return ZenodoPmReferenceStats(
        str(source), str(output), total, written, rejected, timezone_name
    )
