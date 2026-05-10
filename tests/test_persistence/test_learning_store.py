"""Tests for energybrain.persistence.learning_store."""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from energybrain.persistence.learning_store import LearningStore, _from_blob, _to_blob


# ---------------------------------------------------------------------------
# Blob serialisation helpers
# ---------------------------------------------------------------------------

class TestBlobHelpers:
    def test_roundtrip_dict(self):
        obj = {"a": 1, "b": [1, 2, 3]}
        assert _from_blob(_to_blob(obj)) == obj

    def test_roundtrip_list(self):
        obj = list(range(100))
        assert _from_blob(_to_blob(obj)) == obj

    def test_blob_is_bytes(self):
        assert isinstance(_to_blob({"x": 1}), bytes)


# ---------------------------------------------------------------------------
# Helpers — fake model stubs
# ---------------------------------------------------------------------------

def _fake_thermal_model(r2: float = 0.7, trained: bool = True):
    from energybrain.models import ThermalModelParams
    model = MagicMock()
    model._params = ThermalModelParams(r2_score=r2, is_trained=trained, model_type="linear")
    model._model = None
    return model


def _fake_pv_forecaster(calibrated: bool = True, n_obs: int = 5):
    forecaster = MagicMock()
    forecaster.is_calibrated.return_value = calibrated
    forecaster._ridge = MagicMock()
    forecaster._scaler = MagicMock()
    forecaster._calibration_observations = [{}] * n_obs
    return forecaster


def _fake_pattern_learner(days: int = 30):
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.preprocessing import StandardScaler
    learner = MagicMock()
    learner._days_of_data = days
    learner._n_features = 3
    learner._models = {"dhw": GradientBoostingClassifier()}
    learner._scalers = {"dhw": StandardScaler()}
    learner.get_feature_importances.return_value = {"dhw": {"weekday": 0.5}}
    return learner


# ---------------------------------------------------------------------------
# save_thermal_model
# ---------------------------------------------------------------------------

class TestSaveThermalModel:
    @pytest.mark.asyncio
    async def test_no_op_when_no_connection(self):
        db = MagicMock()
        db._conn = None
        store = LearningStore(db)
        model = _fake_thermal_model()
        await store.save_thermal_model(model)  # should not raise

    @pytest.mark.asyncio
    async def test_inserts_row(self):
        conn = AsyncMock()
        conn.execute = AsyncMock()
        conn.commit = AsyncMock()
        db = MagicMock()
        db._conn = conn
        store = LearningStore(db)
        model = _fake_thermal_model()
        await store.save_thermal_model(model)
        conn.execute.assert_called_once()
        conn.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_exception_does_not_propagate(self):
        conn = AsyncMock()
        conn.execute = AsyncMock(side_effect=RuntimeError("db error"))
        db = MagicMock()
        db._conn = conn
        store = LearningStore(db)
        model = _fake_thermal_model()
        await store.save_thermal_model(model)  # should swallow exception


# ---------------------------------------------------------------------------
# load_thermal_model
# ---------------------------------------------------------------------------

class TestLoadThermalModel:
    @pytest.mark.asyncio
    async def test_returns_false_when_no_connection(self):
        db = MagicMock()
        db._conn = None
        store = LearningStore(db)
        model = _fake_thermal_model()
        result = await store.load_thermal_model(model)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_rows(self):
        cur = AsyncMock()
        cur.fetchone = AsyncMock(return_value=None)
        cur.__aenter__ = AsyncMock(return_value=cur)
        cur.__aexit__ = AsyncMock(return_value=False)
        conn = AsyncMock()
        conn.execute = MagicMock(return_value=cur)
        db = MagicMock()
        db._conn = conn
        store = LearningStore(db)
        result = await store.load_thermal_model(_fake_thermal_model())
        assert result is False

    @pytest.mark.asyncio
    async def test_populates_params_from_row(self):
        row = {
            "cooling_rate": 0.4,
            "heating_rate": 0.2,
            "thermal_mass_hours": 6.0,
            "r2_score": 0.8,
            "samples_count": 500,
            "model_blob": None,
            "model_type": "linear",
        }
        cur = AsyncMock()
        cur.fetchone = AsyncMock(return_value=row)
        cur.__aenter__ = AsyncMock(return_value=cur)
        cur.__aexit__ = AsyncMock(return_value=False)
        conn = AsyncMock()
        conn.execute = MagicMock(return_value=cur)
        db = MagicMock()
        db._conn = conn
        store = LearningStore(db)
        model = _fake_thermal_model()
        result = await store.load_thermal_model(model)
        assert result is True
        assert model._params.r2_score == pytest.approx(0.8)
        assert model._params.cooling_rate_c_per_hour == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# save_pv_forecaster
# ---------------------------------------------------------------------------

class TestSavePvForecaster:
    @pytest.mark.asyncio
    async def test_no_op_when_not_calibrated(self):
        db = MagicMock()
        db._conn = AsyncMock()
        store = LearningStore(db)
        forecaster = _fake_pv_forecaster(calibrated=False)
        await store.save_pv_forecaster(forecaster)
        db._conn.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_inserts_when_calibrated(self):
        from sklearn.linear_model import Ridge
        from sklearn.preprocessing import StandardScaler
        conn = AsyncMock()
        conn.execute = AsyncMock()
        conn.commit = AsyncMock()
        db = MagicMock()
        db._conn = conn
        store = LearningStore(db)
        forecaster = MagicMock()
        forecaster.is_calibrated.return_value = True
        forecaster._ridge = Ridge()
        forecaster._scaler = StandardScaler()
        forecaster._calibration_observations = [{}] * 3
        await store.save_pv_forecaster(forecaster)
        conn.execute.assert_called_once()


# ---------------------------------------------------------------------------
# load_pv_forecaster
# ---------------------------------------------------------------------------

class TestLoadPvForecaster:
    @pytest.mark.asyncio
    async def test_returns_false_when_no_connection(self):
        db = MagicMock()
        db._conn = None
        store = LearningStore(db)
        result = await store.load_pv_forecaster(_fake_pv_forecaster())
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_blob(self):
        row = {"ridge_model_blob": None}
        cur = AsyncMock()
        cur.fetchone = AsyncMock(return_value=row)
        cur.__aenter__ = AsyncMock(return_value=cur)
        cur.__aexit__ = AsyncMock(return_value=False)
        conn = AsyncMock()
        conn.execute = MagicMock(return_value=cur)
        db = MagicMock()
        db._conn = conn
        store = LearningStore(db)
        result = await store.load_pv_forecaster(_fake_pv_forecaster())
        assert result is False

    @pytest.mark.asyncio
    async def test_loads_payload_from_blob(self):
        from sklearn.linear_model import Ridge
        from sklearn.preprocessing import StandardScaler
        payload = {"ridge": Ridge(), "scaler": StandardScaler(), "observations": [1, 2, 3]}
        blob = _to_blob(payload)
        row = {"ridge_model_blob": blob}
        cur = AsyncMock()
        cur.fetchone = AsyncMock(return_value=row)
        cur.__aenter__ = AsyncMock(return_value=cur)
        cur.__aexit__ = AsyncMock(return_value=False)
        conn = AsyncMock()
        conn.execute = MagicMock(return_value=cur)
        db = MagicMock()
        db._conn = conn
        store = LearningStore(db)
        forecaster = _fake_pv_forecaster()
        result = await store.load_pv_forecaster(forecaster)
        assert result is True
        assert forecaster._calibration_observations == [1, 2, 3]


# ---------------------------------------------------------------------------
# save_pattern_learner
# ---------------------------------------------------------------------------

class TestSavePatternLearner:
    @pytest.mark.asyncio
    async def test_no_op_when_no_connection(self):
        db = MagicMock()
        db._conn = None
        store = LearningStore(db)
        learner = _fake_pattern_learner()
        await store.save_pattern_learner(learner)  # should not raise

    @pytest.mark.asyncio
    async def test_saves_each_trained_model(self):
        conn = AsyncMock()
        conn.execute = AsyncMock()
        conn.commit = AsyncMock()
        db = MagicMock()
        db._conn = conn
        store = LearningStore(db)
        learner = _fake_pattern_learner()
        await store.save_pattern_learner(learner)
        assert conn.execute.call_count == 1  # only "dhw" model is set


# ---------------------------------------------------------------------------
# load_pattern_learner
# ---------------------------------------------------------------------------

class TestLoadPatternLearner:
    @pytest.mark.asyncio
    async def test_returns_false_when_no_connection(self):
        db = MagicMock()
        db._conn = None
        store = LearningStore(db)
        from energybrain.intelligence.pattern_learner import PatternLearner
        learner = PatternLearner()
        result = await store.load_pattern_learner(learner)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_rows(self):
        cur = AsyncMock()
        cur.fetchone = AsyncMock(return_value=None)
        cur.__aenter__ = AsyncMock(return_value=cur)
        cur.__aexit__ = AsyncMock(return_value=False)
        conn = AsyncMock()
        conn.execute = MagicMock(return_value=cur)
        db = MagicMock()
        db._conn = conn
        store = LearningStore(db)
        from energybrain.intelligence.pattern_learner import PatternLearner
        learner = PatternLearner()
        result = await store.load_pattern_learner(learner)
        assert result is False

    @pytest.mark.asyncio
    async def test_loads_model_from_blob(self):
        from sklearn.ensemble import GradientBoostingClassifier
        from sklearn.preprocessing import StandardScaler
        payload = {"model": GradientBoostingClassifier(), "scaler": StandardScaler(), "n_features": 3}
        blob = _to_blob(payload)
        row = {"model_blob": blob, "samples_count": 45}

        cur = AsyncMock()
        cur.fetchone = AsyncMock(return_value=row)
        cur.__aenter__ = AsyncMock(return_value=cur)
        cur.__aexit__ = AsyncMock(return_value=False)
        conn = AsyncMock()
        conn.execute = MagicMock(return_value=cur)
        db = MagicMock()
        db._conn = conn
        store = LearningStore(db)
        from energybrain.intelligence.pattern_learner import PatternLearner
        learner = PatternLearner()
        result = await store.load_pattern_learner(learner)
        assert result is True
        assert learner._days_of_data == 45
