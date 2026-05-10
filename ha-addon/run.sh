#!/usr/bin/with-contenv bashio

# Read add-on options and export as environment variables for config.py
export HA_URL="$(bashio::config 'ha_url')"
export HA_TOKEN="$(bashio::config 'ha_token')"
export NOTIFICATION_DEVICE="$(bashio::config 'notification_device')"
export MARSTEK_WRITE_ENABLED="$(bashio::config 'marstek_write_enabled')"
export ENTSOE_API_KEY="$(bashio::config 'entsoe_api_key')"
export STATIC_IMPORT_PRICE_EUR_KWH="$(bashio::config 'static_import_price_eur_kwh')"
export STATIC_EXPORT_PRICE_EUR_KWH="$(bashio::config 'static_export_price_eur_kwh')"
export CAPACITY_TARIFF_EUR_KW_YEAR="$(bashio::config 'capacity_tariff_eur_kw_year')"
export LATITUDE="$(bashio::config 'latitude')"
export LONGITUDE="$(bashio::config 'longitude')"
export TIMEZONE="$(bashio::config 'timezone')"
export LOG_LEVEL="$(bashio::config 'log_level')"

# Persistent data directory (mapped to /addon_configs/<slug>)
export DB_PATH="/addon_configs/energybrain/energybrain.db"
mkdir -p /addon_configs/energybrain

bashio::log.info "Starting EnergyBrain v0.3.0"
bashio::log.info "HA URL: ${HA_URL}"
bashio::log.info "Marstek write: ${MARSTEK_WRITE_ENABLED}"

exec python3 -m energybrain.main
