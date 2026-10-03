from __future__ import annotations

import unittest

from iiot_ai_sensor_gateway.foundation_comparators import (
    CATALOG_SCHEMA,
    foundation_model_catalog,
    probe_foundation_environment,
    run_chronos_bolt_zero_shot_v2,
    run_flowstate_zero_shot_v2,
)


class FoundationComparatorTests(unittest.TestCase):
    def test_catalog_is_fail_closed_for_edge_and_production(self) -> None:
        catalog = foundation_model_catalog()
        self.assertEqual(catalog["schema"], CATALOG_SCHEMA)
        by_id = {item["id"]: item for item in catalog["models"]}
        self.assertIn("granite_ttm_r3", by_id)
        self.assertIn("granite_tspulse", by_id)
        self.assertIn("chronos_bolt_tiny", by_id)
        self.assertFalse(by_id["granite_ttm_r3"]["edge_default"])
        self.assertFalse(by_id["granite_ttm_r3"]["production_default"])
        self.assertEqual(
            by_id["granite_tspulse"]["model_path"],
            "ibm-granite/granite-timeseries-tspulse-r1",
        )
        self.assertEqual(by_id["timesfm_3_0"]["model_path"], "google/timesfm-3.0-pytorch")
        self.assertTrue(by_id["timesfm_3_0"]["production_blocked"])

    def test_environment_probe_never_installs_dependencies(self) -> None:
        result = probe_foundation_environment()
        self.assertFalse(result["automatic_install"])
        self.assertIn("granite-tsfm", result["packages"])
        self.assertIn("chronos-forecasting", result["packages"])
        for package in result["packages"].values():
            self.assertIn("installed", package)

    def test_flowstate_requires_explicit_sampling_scale_before_dependency_load(self) -> None:
        with self.assertRaisesRegex(ValueError, "explicit positive scale_factor"):
            run_flowstate_zero_shot_v2("missing.npz", "unused.json", scale_factor=None)

    def test_chronos_dependency_failure_is_actionable_when_absent(self) -> None:
        environment = probe_foundation_environment()["packages"]["chronos-forecasting"]
        if environment["installed"]:
            self.skipTest("chronos-forecasting is installed in this environment")
        with self.assertRaisesRegex(RuntimeError, "chronos-forecasting"):
            run_chronos_bolt_zero_shot_v2("missing.npz", "unused.json")


if __name__ == "__main__":
    unittest.main()
