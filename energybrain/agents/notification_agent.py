"""NotificationAgent — sends push notifications via HA Companion app.

Throttle rules:
  SAFETY_ALARM       — never throttled (always sent immediately)
  SOLAR_OPPORTUNITY  — 1 per day
  DHW_BOOST          — 1 per day
  DAILY_SUMMARY      — 1 per day
  WEEK_STRATEGY      — 1 per week
  MONTHLY_REPORT     — 1 per month
  APPLIANCE_STARTED  — tracked per send() call (caller manages per-run logic)
  All others         — no throttle

Service: notify.mobile_app_{NOTIFICATION_DEVICE}
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from energybrain.agents.base_agent import BaseAgent
from energybrain.config import Config
from energybrain.models import NotificationType
from energybrain.utils.ha_client import HAClient
from energybrain.utils.logging_config import get_logger

logger = get_logger(__name__)

_THROTTLE_DAILY_S = 86_400          # 24 h
_THROTTLE_WEEKLY_S = 7 * 86_400     # 7 days
_THROTTLE_MONTHLY_S = 30 * 86_400   # 30 days

_DAILY_THROTTLED = {
    NotificationType.SOLAR_OPPORTUNITY,
    NotificationType.DHW_BOOST,
    NotificationType.DAILY_SUMMARY,
}


class NotificationAgent(BaseAgent[None]):
    """Sends push notifications and enforces per-type throttling."""

    AGENT_NAME = "notification_agent"

    def __init__(self, ha: HAClient, config: Config) -> None:
        super().__init__(ha)
        self._config = config
        self._last_sent: dict[str, datetime] = {}

    async def collect(self) -> None:
        return None

    async def send(
        self,
        notification_type: NotificationType,
        title: str,
        message: str,
        data: Optional[dict] = None,
    ) -> bool:
        """Send a push notification if not throttled.

        Args:
            notification_type: Type used to select throttle rule.
            title: Notification title shown in the Companion app.
            message: Notification body.
            data: Optional extra data dict (e.g. priority, tag).

        Returns:
            True if the notification was sent, False if throttled.
        """
        if not self._should_send(notification_type):
            self._log.debug("notification_throttled", type=notification_type.value)
            return False

        service_data: dict = {"title": title, "message": message}
        if data:
            service_data["data"] = data

        device = self._config.notification_device
        try:
            await self._ha.call_service(
                "notify",
                f"mobile_app_{device}",
                **service_data,
            )
            self._last_sent[notification_type.value] = datetime.now()
            self._log.info(
                "notification_sent",
                type=notification_type.value,
                title=title,
            )
            return True
        except Exception as exc:
            self._log.error(
                "notification_failed",
                type=notification_type.value,
                error=str(exc),
            )
            return False

    def _should_send(self, notification_type: NotificationType) -> bool:
        """Return True if this notification type is not currently throttled."""
        if notification_type == NotificationType.SAFETY_ALARM:
            return True

        last = self._last_sent.get(notification_type.value)
        if last is None:
            return True

        elapsed = (datetime.now() - last).total_seconds()

        if notification_type in _DAILY_THROTTLED:
            return elapsed >= _THROTTLE_DAILY_S

        if notification_type == NotificationType.WEEK_STRATEGY:
            return elapsed >= _THROTTLE_WEEKLY_S

        if notification_type == NotificationType.MONTHLY_REPORT:
            return elapsed >= _THROTTLE_MONTHLY_S

        return True

    def reset_throttle(self, notification_type: NotificationType) -> None:
        """Clear the throttle for a notification type (e.g. after appliance run ends)."""
        self._last_sent.pop(notification_type.value, None)
