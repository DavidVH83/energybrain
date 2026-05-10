"""OutcomeTracker — closes the feedback loop for all ML models.

Compares every prediction to its actual outcome.
Detects model drift via rolling 14-day accuracy vs 30-day baseline.
Generates monthly AccuracyReport.
Provides sample weights for PatternLearner retraining.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Optional

from energybrain.models import AccuracyReport
from energybrain.utils.logging_config import get_logger

if TYPE_CHECKING:
    from energybrain.intelligence.thermal_model import ThermalModel

logger = get_logger(__name__)

DRIFT_THRESHOLD_PCT = 15.0
DRIFT_WINDOW_DAYS = 14
BASELINE_DAYS = 30

CORRECTION_TOLERANCE: dict[str, float] = {
    "dhw_demand":        0.15,
    "appliance_loading": 0.20,
    "cooking_peak":      1.0,
    "pv_forecast":       0.10,
}

# Model names grouped by accuracy report category
_DHW_MODELS = {"dhw_demand"}
_APPLIANCE_MODELS = {"appliance_loading", "dishwasher", "washing", "dryer"}
_COOKING_MODELS = {"cooking_peak"}
_PV_MODELS = {"pv_forecast"}


class OutcomeTracker:
    """Feedback loop: logs predictions, links outcomes, detects drift, reports accuracy."""

    def __init__(self) -> None:
        # In-memory store; DB persistence goes via DatabaseManager (injected by orchestrator)
        self._predictions: dict[str, dict] = {}
        self._last_drift_check: Optional[datetime] = None
        self._known_drift: set[str] = set()
        self._log = get_logger("outcome_tracker")

    # ------------------------------------------------------------------
    # Core feedback loop
    # ------------------------------------------------------------------

    def log_prediction(
        self,
        model_name: str,
        features: dict,
        predicted_value: float,
    ) -> str:
        """Log prediction. Returns prediction_id for later outcome linking."""
        prediction_id = str(uuid.uuid4())
        self._predictions[prediction_id] = {
            "prediction_id": prediction_id,
            "model_name": model_name,
            "features": features,
            "predicted_value": predicted_value,
            "predicted_at": datetime.now(),
            "actual_value": None,
            "outcome_at": None,
            "is_correct": None,
        }
        return prediction_id

    def log_outcome(self, prediction_id: str, actual_value: float) -> None:
        """Link actual outcome to prediction. Calculates is_correct."""
        rec = self._predictions.get(prediction_id)
        if rec is None:
            self._log.warning("outcome_unknown_prediction", prediction_id=prediction_id)
            return

        model_name = rec["model_name"]
        tolerance = CORRECTION_TOLERANCE.get(model_name, 0.15)
        predicted = rec["predicted_value"]

        # For probability predictions: absolute difference; for regression: relative
        if model_name in ("cooking_peak",):
            is_correct = abs(predicted - actual_value) <= tolerance
        elif model_name == "pv_forecast":
            # Relative tolerance — avoid division by zero
            base = max(abs(actual_value), 0.001)
            is_correct = abs(predicted - actual_value) / base <= tolerance
        else:
            is_correct = abs(predicted - actual_value) <= tolerance

        rec["actual_value"] = actual_value
        rec["outcome_at"] = datetime.now()
        rec["is_correct"] = is_correct

    # ------------------------------------------------------------------
    # Drift detection
    # ------------------------------------------------------------------

    def check_drift(self) -> dict[str, bool]:
        """Return {model_name: drift_detected} for all models with enough data.

        Called daily at 02:00 before PatternLearner retraining.
        """
        now = datetime.now()
        drift_result: dict[str, bool] = {}
        new_drift: set[str] = set()

        model_names = {r["model_name"] for r in self._predictions.values()}
        for model in model_names:
            drift = self._check_model_drift(model, now)
            drift_result[model] = drift
            if drift:
                new_drift.add(model)

        newly_detected = new_drift - self._known_drift
        if newly_detected:
            self._log.warning("drift_detected", models=list(newly_detected))
        self._known_drift = new_drift
        self._last_drift_check = now
        return drift_result

    def _check_model_drift(self, model_name: str, now: datetime) -> bool:
        """True if recent accuracy dropped > DRIFT_THRESHOLD_PCT vs baseline."""
        recent_cutoff = now - timedelta(days=DRIFT_WINDOW_DAYS)
        baseline_cutoff = now - timedelta(days=BASELINE_DAYS)

        recent = [
            r for r in self._predictions.values()
            if r["model_name"] == model_name
            and r["is_correct"] is not None
            and r["outcome_at"] is not None
            and r["outcome_at"] >= recent_cutoff
        ]
        baseline = [
            r for r in self._predictions.values()
            if r["model_name"] == model_name
            and r["is_correct"] is not None
            and r["outcome_at"] is not None
            and baseline_cutoff <= r["outcome_at"] < recent_cutoff
        ]
        if len(recent) < 5 or len(baseline) < 10:
            return False  # Not enough data

        recent_acc = sum(1 for r in recent if r["is_correct"]) / len(recent) * 100
        baseline_acc = sum(1 for r in baseline if r["is_correct"]) / len(baseline) * 100
        drop = baseline_acc - recent_acc
        return drop > DRIFT_THRESHOLD_PCT

    # ------------------------------------------------------------------
    # Accuracy report
    # ------------------------------------------------------------------

    def get_accuracy_report(self, period_days: int = 30) -> AccuracyReport:
        """Full accuracy report for last N days."""
        now = datetime.now()
        cutoff = now - timedelta(days=period_days)

        completed = [
            r for r in self._predictions.values()
            if r["is_correct"] is not None
            and r["predicted_at"] >= cutoff
        ]

        def _acc(model_set: set[str]) -> float:
            recs = [r for r in completed if r["model_name"] in model_set]
            if not recs:
                return 0.0
            return sum(1 for r in recs if r["is_correct"]) / len(recs) * 100

        drift = self.check_drift()

        return AccuracyReport(
            period_start=cutoff,
            period_end=now,
            dhw_accuracy_pct=_acc(_DHW_MODELS),
            appliance_loading_accuracy_pct=_acc(_APPLIANCE_MODELS),
            pv_forecast_accuracy_pct=_acc(_PV_MODELS),
            cooking_peak_accuracy_pct=_acc(_COOKING_MODELS),
            drift_detected=drift,
            total_predictions=len(completed),
            estimated_savings_eur=0.0,  # Calculated by orchestrator with tariff data
        )

    # ------------------------------------------------------------------
    # Thermal model upgrade trigger
    # ------------------------------------------------------------------

    def trigger_thermal_model_upgrade(self, thermal_model: "ThermalModel") -> bool:
        """Call after 90 days. Returns True if GBR upgrade was performed."""
        upgraded = thermal_model.evaluate_upgrade()
        if upgraded:
            self._log.info("thermal_model_upgrade_triggered_by_outcome_tracker")
        return upgraded

    # ------------------------------------------------------------------
    # Sample weights for PatternLearner retraining
    # ------------------------------------------------------------------

    def get_model_feedback_weights(self, model_name: str) -> list[float]:
        """Sample weights for PatternLearner retraining.

        Recent correct predictions get weight 1.2; recent incorrect get 0.8.
        Older predictions retain weight 1.0.
        """
        now = datetime.now()
        recent_cutoff = now - timedelta(days=DRIFT_WINDOW_DAYS)
        weights = []
        for rec in self._predictions.values():
            if rec["model_name"] != model_name or rec["is_correct"] is None:
                continue
            if rec["predicted_at"] >= recent_cutoff:
                weights.append(1.2 if rec["is_correct"] else 0.8)
            else:
                weights.append(1.0)
        return weights
