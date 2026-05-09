"""Tests for energybrain.agents.notification_agent."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from energybrain.agents.notification_agent import NotificationAgent
from energybrain.config import load_config
from energybrain.exceptions import HAConnectionError
from energybrain.models import NotificationType
from energybrain.utils.ha_client import HAClient


def _make_ha(fail: bool = False) -> HAClient:
    ha = MagicMock(spec=HAClient)
    if fail:
        ha.call_service = AsyncMock(side_effect=HAConnectionError("timeout"))
    else:
        ha.call_service = AsyncMock(return_value=[])
    return ha


class TestNotificationAgentSend:
    async def test_send_returns_true_on_success(self, minimal_env):
        agent = NotificationAgent(_make_ha(), load_config())
        result = await agent.send(
            NotificationType.DAILY_SUMMARY,
            "Test title",
            "Test message",
        )
        assert result is True

    async def test_send_calls_ha_notify_service(self, minimal_env):
        ha = _make_ha()
        agent = NotificationAgent(ha, load_config())
        await agent.send(NotificationType.DAILY_SUMMARY, "Title", "Body")
        ha.call_service.assert_awaited_once()
        args = ha.call_service.await_args[0]
        assert args[0] == "notify"
        assert args[1] == "mobile_app_test_device"

    async def test_send_returns_false_on_connection_error(self, minimal_env):
        agent = NotificationAgent(_make_ha(fail=True), load_config())
        result = await agent.send(NotificationType.DAILY_SUMMARY, "Title", "Body")
        assert result is False

    async def test_send_includes_title_and_message(self, minimal_env):
        ha = _make_ha()
        agent = NotificationAgent(ha, load_config())
        await agent.send(NotificationType.DAILY_SUMMARY, "My Title", "My Body")
        kwargs = ha.call_service.await_args[1]
        assert kwargs["title"] == "My Title"
        assert kwargs["message"] == "My Body"

    async def test_send_includes_data_when_provided(self, minimal_env):
        ha = _make_ha()
        agent = NotificationAgent(ha, load_config())
        await agent.send(
            NotificationType.SAFETY_ALARM,
            "Alarm",
            "Something wrong",
            data={"priority": "high"},
        )
        kwargs = ha.call_service.await_args[1]
        assert kwargs["data"]["priority"] == "high"


class TestNotificationThrottle:
    async def test_safety_alarm_always_sent(self, minimal_env):
        ha = _make_ha()
        agent = NotificationAgent(ha, load_config())
        # Send twice — both should succeed
        r1 = await agent.send(NotificationType.SAFETY_ALARM, "Alarm", "msg")
        r2 = await agent.send(NotificationType.SAFETY_ALARM, "Alarm", "msg")
        assert r1 is True
        assert r2 is True
        assert ha.call_service.await_count == 2

    async def test_solar_opportunity_throttled_after_first_send(self, minimal_env):
        ha = _make_ha()
        agent = NotificationAgent(ha, load_config())
        r1 = await agent.send(NotificationType.SOLAR_OPPORTUNITY, "Sun", "msg")
        r2 = await agent.send(NotificationType.SOLAR_OPPORTUNITY, "Sun", "msg")
        assert r1 is True
        assert r2 is False  # throttled

    async def test_solar_opportunity_sent_again_after_24h(self, minimal_env):
        ha = _make_ha()
        agent = NotificationAgent(ha, load_config())
        # First send
        await agent.send(NotificationType.SOLAR_OPPORTUNITY, "Sun", "msg")
        # Simulate 25 hours passed
        agent._last_sent[NotificationType.SOLAR_OPPORTUNITY.value] = (
            datetime.now() - timedelta(hours=25)
        )
        r2 = await agent.send(NotificationType.SOLAR_OPPORTUNITY, "Sun", "msg")
        assert r2 is True

    async def test_dhw_boost_throttled_to_1_per_day(self, minimal_env):
        ha = _make_ha()
        agent = NotificationAgent(ha, load_config())
        await agent.send(NotificationType.DHW_BOOST, "DHW", "msg")
        r2 = await agent.send(NotificationType.DHW_BOOST, "DHW", "msg")
        assert r2 is False

    async def test_daily_summary_throttled_to_1_per_day(self, minimal_env):
        ha = _make_ha()
        agent = NotificationAgent(ha, load_config())
        await agent.send(NotificationType.DAILY_SUMMARY, "Summary", "msg")
        r2 = await agent.send(NotificationType.DAILY_SUMMARY, "Summary", "msg")
        assert r2 is False

    async def test_week_strategy_throttled_to_1_per_week(self, minimal_env):
        ha = _make_ha()
        agent = NotificationAgent(ha, load_config())
        await agent.send(NotificationType.WEEK_STRATEGY, "Week", "msg")
        r2 = await agent.send(NotificationType.WEEK_STRATEGY, "Week", "msg")
        assert r2 is False

    async def test_month_report_throttled_to_1_per_month(self, minimal_env):
        ha = _make_ha()
        agent = NotificationAgent(ha, load_config())
        await agent.send(NotificationType.MONTHLY_REPORT, "Report", "msg")
        r2 = await agent.send(NotificationType.MONTHLY_REPORT, "Report", "msg")
        assert r2 is False

    async def test_reset_throttle_allows_resend(self, minimal_env):
        ha = _make_ha()
        agent = NotificationAgent(ha, load_config())
        await agent.send(NotificationType.DHW_BOOST, "DHW", "msg")
        agent.reset_throttle(NotificationType.DHW_BOOST)
        r2 = await agent.send(NotificationType.DHW_BOOST, "DHW", "msg")
        assert r2 is True

    async def test_collect_returns_none(self, minimal_env):
        agent = NotificationAgent(_make_ha(), load_config())
        assert await agent.collect() is None
