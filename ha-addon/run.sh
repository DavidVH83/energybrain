#!/bin/bash
set -e

OPTIONS=/data/options.json

# Read add-on options written by HA supervisor to /data/options.json
export HA_URL=$(jq -r '.ha_url'                           "$OPTIONS")
export HA_TOKEN=$(jq -r '.ha_token'                       "$OPTIONS")
export NOTIFICATION_DEVICE=$(jq -r '.notification_device' "$OPTIONS")
export MARSTEK_WRITE_ENABLED=$(jq -r '.marstek_write_enabled' "$OPTIONS")
export ENTSOE_API_KEY=$(jq -r '.entsoe_api_key'           "$OPTIONS")
export STATIC_IMPORT_PRICE_EUR_KWH=$(jq -r '.static_import_price_eur_kwh' "$OPTIONS")
export STATIC_EXPORT_PRICE_EUR_KWH=$(jq -r '.static_export_price_eur_kwh' "$OPTIONS")
export CAPACITY_TARIFF_EUR_KW_YEAR=$(jq -r '.capacity_tariff_eur_kw_year' "$OPTIONS")
export LATITUDE=$(jq -r '.latitude'                       "$OPTIONS")
export LONGITUDE=$(jq -r '.longitude'                     "$OPTIONS")
export TIMEZONE=$(jq -r '.timezone'                       "$OPTIONS")
export LOG_LEVEL=$(jq -r '.log_level'                     "$OPTIONS")

# Persistent data directory (mapped via 'map: addon_config:rw' in config.yaml)
export DB_PATH="/addon_configs/energybrain/energybrain.db"
mkdir -p /addon_configs/energybrain

echo "[EnergyBrain] Starting v0.3.1"
echo "[EnergyBrain] HA URL: ${HA_URL}"
echo "[EnergyBrain] Marstek write: ${MARSTEK_WRITE_ENABLED}"

exec python3 -m energybrain.main
