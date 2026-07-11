from __future__ import annotations

from typing import Any


def baseline_candidates(
    x: Any,
    target_indices: Any,
    *,
    horizon_steps: int,
    seasonal_period: int = 0,
) -> dict[str, dict[str, Any]]:
    """Build independent, explicitly-applicable forecasting baselines."""

    import numpy as np

    if horizon_steps < 1:
        raise ValueError("horizon_steps must be >= 1")
    if seasonal_period < 0:
        raise ValueError("seasonal_period must be >= 0")
    if x.ndim != 3 or x.shape[1] < 1:
        raise ValueError("baseline input must be [samples, sequence, features]")

    last = x[:, -1, target_indices]
    candidates: dict[str, dict[str, Any]] = {
        "last_value": {
            "applicable": True,
            "reason": None,
            "predictions": last,
        },
        "window_mean": {
            "applicable": True,
            "reason": None,
            "predictions": np.mean(x[:, :, target_indices], axis=1),
        },
    }
    if x.shape[1] >= 2:
        slope = last - x[:, -2, target_indices]
        candidates["drift"] = {
            "applicable": True,
            "reason": None,
            "predictions": last + horizon_steps * slope,
        }
    else:
        candidates["drift"] = {
            "applicable": False,
            "reason": "requires_at_least_two_history_steps",
            "predictions": None,
        }

    if seasonal_period <= 0:
        candidates["seasonal_naive"] = {
            "applicable": False,
            "reason": "seasonal_period_not_configured",
            "predictions": None,
        }
    else:
        lag_from_input_end = seasonal_period - horizon_steps
        if lag_from_input_end < 0:
            candidates["seasonal_naive"] = {
                "applicable": False,
                "reason": "seasonal_period_shorter_than_forecast_horizon",
                "predictions": None,
            }
        elif lag_from_input_end == 0:
            candidates["seasonal_naive"] = {
                "applicable": False,
                "reason": "prediction_identical_to_last_value_for_period_equal_horizon",
                "predictions": None,
            }
        elif x.shape[1] < lag_from_input_end:
            candidates["seasonal_naive"] = {
                "applicable": False,
                "reason": (
                    f"requires_history_{lag_from_input_end}_steps_but_window_has_{x.shape[1]}"
                ),
                "predictions": None,
            }
        else:
            candidates["seasonal_naive"] = {
                "applicable": True,
                "reason": None,
                "predictions": x[:, -lag_from_input_end, target_indices],
            }
    return candidates


def select_baseline_per_target(
    actual: Any,
    candidates: dict[str, dict[str, Any]],
    target_names: tuple[str, ...],
) -> dict[str, Any]:
    """Select a baseline on validation data, independently per target.

    Duplicate prediction arrays are excluded so two aliases cannot count as
    independent evidence.
    """

    import numpy as np

    selected: dict[str, str] = {}
    applicability: dict[str, Any] = {}
    unique_candidates: dict[str, Any] = {}
    duplicate_of: dict[str, str] = {}
    for name, item in candidates.items():
        applicable = bool(item.get("applicable")) and item.get("predictions") is not None
        applicability[name] = {
            "applicable": applicable,
            "reason": item.get("reason"),
        }
        if not applicable:
            continue
        predictions = item["predictions"]
        duplicate = next(
            (
                existing_name
                for existing_name, existing in unique_candidates.items()
                if predictions.shape == existing.shape and np.allclose(predictions, existing)
            ),
            None,
        )
        if duplicate is not None:
            duplicate_of[name] = duplicate
            applicability[name] = {
                "applicable": False,
                "reason": f"duplicate_predictions_of_{duplicate}",
            }
            continue
        unique_candidates[name] = predictions

    if not unique_candidates:
        raise ValueError("no applicable independent baseline candidates")
    for target_index, target_name in enumerate(target_names):
        selected[target_name] = min(
            unique_candidates,
            key=lambda name: float(
                np.sqrt(
                    np.mean(
                        (
                            unique_candidates[name][:, target_index]
                            - actual[:, target_index]
                        )
                        ** 2
                    )
                )
            ),
        )
    return {
        "selected_by_target": selected,
        "applicability": applicability,
        "duplicate_of": duplicate_of,
    }


def compose_selected_baseline(
    candidates: dict[str, dict[str, Any]],
    selection: dict[str, Any],
    target_names: tuple[str, ...],
) -> Any:
    import numpy as np

    columns = []
    for target_index, target_name in enumerate(target_names):
        baseline_name = selection["selected_by_target"][target_name]
        item = candidates.get(baseline_name)
        if not item or not item.get("applicable") or item.get("predictions") is None:
            raise ValueError(f"selected baseline {baseline_name} unavailable for {target_name}")
        columns.append(item["predictions"][:, target_index])
    return np.stack(columns, axis=1)
