from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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
class UciAirQualityStats:
    source: str
    output: str
    total_rows: int
    written_rows: int
    rejected_rows: int
    missing_values: int
    timezone_assumption: str

    def as_dict(self):
        return asdict(self)


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    text = value.strip().replace(",", ".")
    if not text:
        return None
    number = float(text)
    return None if number == -200 else number


def _timestamp(date_value: str, time_value: str, timezone_name: str) -> str:
    local = datetime.strptime(f"{date_value.strip()} {time_value.strip()}", "%d/%m/%Y %H.%M.%S")
    return local.replace(tzinfo=ZoneInfo(timezone_name)).isoformat()


def adapt_uci_air_quality_csv(
    input_csv: str | Path,
    output_jsonl: str | Path,
    *,
    timezone_name: str = "Europe/Rome",
) -> UciAirQualityStats:
    source = Path(input_csv)
    output = Path(output_jsonl)
    output.parent.mkdir(parents=True, exist_ok=True)
    total = written = rejected = missing = 0
    with source.open(encoding="utf-8-sig", newline="") as input_file, output.open(
        "w", encoding="utf-8"
    ) as output_file:
        reader = csv.DictReader(input_file, delimiter=";")
        required = {"Date", "Time", "T", "RH"}
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise ValueError(f"UCI CSV is missing required columns: {sorted(required)}")
        for row_number, row in enumerate(reader, start=2):
            if not any((value or "").strip() for value in row.values()):
                continue
            total += 1
            try:
                timestamp = _timestamp(row["Date"], row["Time"], timezone_name)
                sensor = {name: None for name in CANONICAL_FIELDS}
                sensor["temperature_c"] = _number(row.get("T"))
                sensor["humidity_pct"] = _number(row.get("RH"))
                reference = {
                    "co_mg_m3": _number(row.get("CO(GT)")),
                    "no2_ug_m3": _number(row.get("NO2(GT)")),
                    "nox_ppb": _number(row.get("NOx(GT)")),
                    "absolute_humidity": _number(row.get("AH")),
                    "pt08_s1_co_response": _number(row.get("PT08.S1(CO)")),
                    "pt08_s3_nox_response": _number(row.get("PT08.S3(NOx)")),
                    "pt08_s4_no2_response": _number(row.get("PT08.S4(NO2)")),
                    "pt08_s5_o3_targeted_response": _number(row.get("PT08.S5(O3)")),
                }
            except (KeyError, TypeError, ValueError, OverflowError):
                rejected += 1
                continue
            missing += sum(value is None for value in sensor.values())
            missing += sum(value is None for value in reference.values())
            record = {
                "schema_version": "iiot.dataset_record.v1",
                "dataset_id": "uci_air_quality_360",
                "lane": "air_reference_regression",
                "record_id": f"uci-aq-{row_number}",
                "timestamp": timestamp,
                "device_id": "uci_air_quality_field_device",
                "site_id": "italian_city_roadside_site",
                "sensor": sensor,
                "reference": reference,
                "quality": {
                    "valid": sensor["temperature_c"] is not None
                    and sensor["humidity_pct"] is not None,
                    "missing_marker": -200,
                    "unit_conversion_applied": False,
                    "notes": [
                        "CO reference remains mg/m3; co_ppm is null.",
                        "NO2 reference remains ug/m3; project NO2 signal fields are null.",
                        "PT08.S5 is a nominally O3-targeted response, not O3 ground truth.",
                    ],
                },
                "provenance": {
                    "source_file": str(source),
                    "source_row": row_number,
                    "timezone_assumption": timezone_name,
                    "license": "CC BY 4.0",
                },
            }
            output_file.write(json.dumps(record, separators=(",", ":")) + "\n")
            written += 1
    return UciAirQualityStats(
        str(source), str(output), total, written, rejected, missing, timezone_name
    )
