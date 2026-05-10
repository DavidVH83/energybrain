"""Tests for energybrain.intelligence.outcome_tracker."""
from datetime import datetime, timedelta

import pytest

from energybrain.intelligence.outcome_tracker import (
    CORRECTION_TOLERANCE,
    DRIFT_THRESHOLD_PCT,
    OutcomeTracker,
)


class TestLogPrediction:
    def test_returns_string_id(self):
        ot = OutcomeTracker()
        pid = ot.log_prediction("dhw_demand", {"hour": 8}, 0.7)
        assert isinstance(pid, str)
        assert len(pid) == 36  # UUID format

    def test_prediction_stored(self):
        ot = OutcomeTracker()
        pid = ot.log_prediction("dhw_demand", {"hour": 8}, 0.7)
        assert pid in ot._predictions

    def test_prediction_fields_correct(self):
        ot = OutcomeTracker()
        features = {"hour": 8, "weekday": 1}
        pid = ot.log_prediction("dhw_demand", features, 0.7)
        rec = ot._predictions[pid]
        assert rec["model_name"] == "dhw_demand"
        assert rec["features"] == features
        assert rec["predicted_value"] == pytest.approx(0.7)
        assert rec["actual_value"] is None
        assert rec["is_correct"] is None

    def test_two_predictions_have_different_ids(self):
        ot = OutcomeTracker()
        p1 = ot.log_prediction("dhw_demand", {}, 0.5)
        p2 = ot.log_prediction("dhw_demand", {}, 0.6)
        assert p1 != p2


class TestLogOutcome:
    def test_outcome_sets_actual_value(self):
        ot = OutcomeTracker()
        pid = ot.log_prediction("dhw_demand", {}, 0.7)
        ot.log_outcome(pid, 1.0)
        assert ot._predictions[pid]["actual_value"] == pytest.approx(1.0)

    def test_correct_within_tolerance(self):
        ot = OutcomeTracker()
        tol = CORRECTION_TOLERANCE["dhw_demand"]
        pid = ot.log_prediction("dhw_demand", {}, 0.7)
        ot.log_outcome(pid, 0.7 + tol - 0.01)
        assert ot._predictions[pid]["is_correct"] is True

    def test_incorrect_outside_tolerance(self):
        ot = OutcomeTracker()
        tol = CORRECTION_TOLERANCE["dhw_demand"]
        pid = ot.log_prediction("dhw_demand", {}, 0.7)
        ot.log_outcome(pid, 0.7 + tol + 0.01)
        assert ot._predictions[pid]["is_correct"] is False

    def test_cooking_peak_uses_hour_tolerance(self):
        ot = OutcomeTracker()
        tol = CORRECTION_TOLERANCE["cooking_peak"]
        pid = ot.log_prediction("cooking_peak", {}, 18.0)
        ot.log_outcome(pid, 18.0 + tol - 0.1)
        assert ot._predictions[pid]["is_correct"] is True

    def test_cooking_peak_incorrect(self):
        ot = OutcomeTracker()
        tol = CORRECTION_TOLERANCE["cooking_peak"]
        pid = ot.log_prediction("cooking_peak", {}, 18.0)
        ot.log_outcome(pid, 18.0 + tol + 0.5)
        assert ot._predictions[pid]["is_correct"] is False

    def test_pv_forecast_relative_tolerance(self):
        ot = OutcomeTracker()
        tol = CORRECTION_TOLERANCE["pv_forecast"]
        pid = ot.log_prediction("pv_forecast", {}, 10.0)
        ot.log_outcome(pid, 10.0 * (1 + tol - 0.01))
        assert ot._predictions[pid]["is_correct"] is True

    def test_unknown_prediction_id_does_not_raise(self):
        ot = OutcomeTracker()
        ot.log_outcome("nonexistent-id", 0.5)  # Should not raise

    def test_outcome_at_set(self):
        ot = OutcomeTracker()
        pid = ot.log_prediction("dhw_demand", {}, 0.5)
        ot.log_outcome(pid, 0.5)
        assert ot._predictions[pid]["outcome_at"] is not None


class TestCheckDrift:
    def _fill_predictions(
        self, ot: OutcomeTracker, model: str, n_baseline: int, n_recent: int,
        baseline_correct_pct: float, recent_correct_pct: float
    ):
        now = datetime.now()
        # Baseline: 15-29 days ago
        for i in range(n_baseline):
            pid = ot.log_prediction(model, {}, 0.7)
            rec = ot._predictions[pid]
            rec["predicted_at"] = now - timedelta(days=20)
            rec["outcome_at"] = now - timedelta(days=20)
            rec["actual_value"] = 0.7
            rec["is_correct"] = i < int(n_baseline * baseline_correct_pct / 100)

        # Recent: last 7 days
        for i in range(n_recent):
            pid = ot.log_prediction(model, {}, 0.7)
            rec = ot._predictions[pid]
            rec["predicted_at"] = now - timedelta(days=3)
            rec["outcome_at"] = now - timedelta(days=3)
            rec["actual_value"] = 0.7
            rec["is_correct"] = i < int(n_recent * recent_correct_pct / 100)

    def test_no_drift_when_both_accurate(self):
        ot = OutcomeTracker()
        self._fill_predictions(ot, "dhw_demand", 20, 10, 90.0, 85.0)
        drift = ot.check_drift()
        assert drift.get("dhw_demand", False) is False

    def test_drift_detected_when_accuracy_drops(self):
        ot = OutcomeTracker()
        self._fill_predictions(ot, "dhw_demand", 20, 10, 90.0, 60.0)
        drift = ot.check_drift()
        assert drift.get("dhw_demand", False) is True

    def test_no_drift_with_insufficient_data(self):
        ot = OutcomeTracker()
        # Only 3 recent, 5 baseline — below minimums
        self._fill_predictions(ot, "pv_forecast", 5, 3, 90.0, 50.0)
        drift = ot.check_drift()
        assert drift.get("pv_forecast", False) is False

    def test_returns_dict_per_model(self):
        ot = OutcomeTracker()
        self._fill_predictions(ot, "dhw_demand", 20, 10, 90.0, 70.0)
        self._fill_predictions(ot, "pv_forecast", 20, 10, 90.0, 88.0)
        drift = ot.check_drift()
        assert "dhw_demand" in drift
        assert "pv_forecast" in drift


class TestGetAccuracyReport:
    def test_empty_tracker_returns_zero_accuracy(self):
        ot = OutcomeTracker()
        report = ot.get_accuracy_report()
        assert report.dhw_accuracy_pct == pytest.approx(0.0)
        assert report.total_predictions == 0

    def test_accuracy_calculated_for_dhw(self):
        ot = OutcomeTracker()
        tol = CORRECTION_TOLERANCE["dhw_demand"]
        for _ in range(8):
            pid = ot.log_prediction("dhw_demand", {}, 0.7)
            ot.log_outcome(pid, 0.7)  # All correct
        for _ in range(2):
            pid = ot.log_prediction("dhw_demand", {}, 0.7)
            ot.log_outcome(pid, 1.0)  # Wrong
        report = ot.get_accuracy_report()
        assert report.dhw_accuracy_pct == pytest.approx(80.0)

    def test_report_has_correct_period(self):
        ot = OutcomeTracker()
        report = ot.get_accuracy_report(period_days=14)
        delta = report.period_end - report.period_start
        assert abs(delta.days - 14) <= 1

    def test_total_predictions_count(self):
        ot = OutcomeTracker()
        for _ in range(5):
            pid = ot.log_prediction("pv_forecast", {}, 10.0)
            ot.log_outcome(pid, 10.0)
        report = ot.get_accuracy_report()
        assert report.total_predictions == 5


class TestTriggerThermalModelUpgrade:
    def test_upgrade_called_when_r2_low(self):
        ot = OutcomeTracker()
        from unittest.mock import MagicMock
        model = MagicMock()
        model.evaluate_upgrade.return_value = True
        result = ot.trigger_thermal_model_upgrade(model)
        assert result is True
        model.evaluate_upgrade.assert_called_once()

    def test_no_upgrade_when_r2_good(self):
        ot = OutcomeTracker()
        from unittest.mock import MagicMock
        model = MagicMock()
        model.evaluate_upgrade.return_value = False
        result = ot.trigger_thermal_model_upgrade(model)
        assert result is False


class TestGetModelFeedbackWeights:
    def test_empty_returns_empty(self):
        ot = OutcomeTracker()
        weights = ot.get_model_feedback_weights("dhw_demand")
        assert weights == []

    def test_recent_correct_gets_higher_weight(self):
        ot = OutcomeTracker()
        pid = ot.log_prediction("dhw_demand", {}, 0.7)
        ot.log_outcome(pid, 0.7)
        weights = ot.get_model_feedback_weights("dhw_demand")
        assert weights == [pytest.approx(1.2)]

    def test_recent_incorrect_gets_lower_weight(self):
        ot = OutcomeTracker()
        pid = ot.log_prediction("dhw_demand", {}, 0.7)
        ot.log_outcome(pid, 1.0)  # Wrong by more than tolerance
        weights = ot.get_model_feedback_weights("dhw_demand")
        assert weights == [pytest.approx(0.8)]

    def test_old_predictions_get_neutral_weight(self):
        ot = OutcomeTracker()
        pid = ot.log_prediction("dhw_demand", {}, 0.7)
        ot.log_outcome(pid, 0.7)
        # Make it old
        ot._predictions[pid]["predicted_at"] = datetime.now() - timedelta(days=20)
        weights = ot.get_model_feedback_weights("dhw_demand")
        assert weights == [pytest.approx(1.0)]
