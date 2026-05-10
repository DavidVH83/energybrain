"""Train all EnergyBrain ML models on stored DB data and save results.

Usage:
    python scripts/train_models.py
    python scripts/train_models.py --db path/to/energybrain.db
    python scripts/train_models.py --dry-run

Trains in order:
    1. ThermalModel (needs ≥336 readings ≈ 14 days at 60s)
    2. PatternLearner (needs ≥14 days of daily data)
    3. PVForecaster calibration (passive — calibrated daily at 21:00 automatically)

Saves trained models back to the DB so the orchestrator loads them on next start.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from energybrain.config import ConfigError, load_config
from energybrain.intelligence.pattern_learner import PatternLearner
from energybrain.intelligence.thermal_model import ThermalModel
from energybrain.persistence.database import DatabaseManager
from energybrain.persistence.learning_store import LearningStore
from energybrain.persistence.state_store import StateStore
from energybrain.utils.logging_config import setup_logging


async def _run(db_path: Path, dry_run: bool) -> int:
    setup_logging("INFO")
    print(f"EnergyBrain — Model Training")
    print(f"DB: {db_path}")
    if dry_run:
        print("[DRY RUN] Models will be trained but NOT saved to DB\n")

    if not db_path.exists():
        print(f"[ERROR] Database not found: {db_path}")
        print("  Run the orchestrator first to collect data.")
        return 1

    db = DatabaseManager(db_path)
    await db.initialize()

    state_store = StateStore(db)
    learning_store = LearningStore(db)

    exit_code = 0

    # ------------------------------------------------------------------
    # ThermalModel
    # ------------------------------------------------------------------
    print("\n── ThermalModel ──────────────────────────────────────")
    thermal = ThermalModel()
    observations = await state_store.build_thermal_observations(days=90)
    print(f"  Observations available: {len(observations)}")
    if len(observations) < 50:
        print(f"  [SKIP] Need ≥50 observations (got {len(observations)})")
        print("         Collect ≈ 1h of data at 60s interval to start training")
    else:
        success = thermal.update_model(observations)
        if success:
            params = thermal._params
            print(f"  Model type:  {params.model_type}")
            print(f"  R² score:    {params.r2_score:.4f}")
            print(f"  Samples:     {params.samples_count}")
            print(f"  Cooling rate: {params.cooling_rate_c_per_hour:.3f} °C/h")
            print(f"  Heating rate: {params.heating_rate_c_per_hour:.3f} °C/h")
            if not dry_run:
                await learning_store.save_thermal_model(thermal)
                print("  Saved to DB ✓")
            else:
                print("  [DRY RUN] Not saved")
        else:
            print(f"  [FAIL] Training failed")
            exit_code = 1

    # ------------------------------------------------------------------
    # PatternLearner
    # ------------------------------------------------------------------
    print("\n── PatternLearner ────────────────────────────────────")
    learner = PatternLearner()
    training_rows = await state_store.build_pattern_training_data(days=90)
    print(f"  Training days available: {len(training_rows)}")
    if len(training_rows) < 14:
        print(f"  [SKIP] Need ≥14 days (got {len(training_rows)})")
        print("         Collect ≈ 2 weeks of data to start training")
    else:
        learner.update_patterns(training_rows)
        importances = learner.get_feature_importances()
        print(f"  Days of data: {learner._days_of_data}")
        models_trained = [k for k in learner._models if learner._models[k] is not None]
        print(f"  Models trained: {models_trained}")
        if importances.get("dhw"):
            top = sorted(importances["dhw"].items(), key=lambda x: x[1], reverse=True)[:3]
            print(f"  DHW top features: {[(k, round(v, 3)) for k, v in top]}")
        if not dry_run:
            await learning_store.save_pattern_learner(learner)
            print("  Saved to DB ✓")
        else:
            print("  [DRY RUN] Not saved")

    # ------------------------------------------------------------------
    # PVForecaster
    # ------------------------------------------------------------------
    print("\n── PVForecaster ──────────────────────────────────────")
    print("  PVForecaster calibrates automatically at 21:00 each day.")
    print("  Run the orchestrator for ≥30 days to accumulate calibration data.")
    print("  Use --dry-run=false to force-save any existing calibration.")

    await db.close()

    print(f"\n{'OK' if exit_code == 0 else 'ERRORS'} — training complete")
    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(description="EnergyBrain model training")
    parser.add_argument(
        "--db", type=Path, default=None,
        help="Path to energybrain.db (default: from DB_PATH env or energybrain.db)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Train but do not save to DB"
    )
    args = parser.parse_args()

    db_path = args.db
    if db_path is None:
        try:
            config = load_config()
            db_path = config.db_path
        except ConfigError:
            db_path = Path("energybrain.db")

    sys.exit(asyncio.run(_run(db_path, args.dry_run)))


if __name__ == "__main__":
    main()
