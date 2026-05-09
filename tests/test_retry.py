"""Tests for energybrain.utils.retry."""
import asyncio
from unittest.mock import AsyncMock, call

import pytest

from energybrain.exceptions import RetryExhaustedError
from energybrain.utils.retry import retry_async, with_retry


class TestRetryAsync:
    async def test_succeeds_on_first_attempt(self):
        mock = AsyncMock(return_value=42)
        result = await retry_async(mock, max_attempts=3, initial_delay_s=0)
        assert result == 42
        mock.assert_awaited_once()

    async def test_retries_on_failure_then_succeeds(self):
        mock = AsyncMock(side_effect=[ValueError("fail"), ValueError("fail"), 99])
        result = await retry_async(mock, max_attempts=3, initial_delay_s=0)
        assert result == 99
        assert mock.await_count == 3

    async def test_raises_retry_exhausted_when_all_fail(self):
        mock = AsyncMock(side_effect=ConnectionError("down"))
        with pytest.raises(RetryExhaustedError) as exc_info:
            await retry_async(mock, max_attempts=3, initial_delay_s=0)
        assert exc_info.value.attempts == 3
        assert isinstance(exc_info.value.last_error, ConnectionError)

    async def test_only_retries_specified_exceptions(self):
        mock = AsyncMock(side_effect=ValueError("not retryable"))
        with pytest.raises(ValueError):
            await retry_async(
                mock,
                max_attempts=3,
                initial_delay_s=0,
                retryable_exceptions=(ConnectionError,),
            )
        mock.assert_awaited_once()

    async def test_passes_args_and_kwargs(self):
        mock = AsyncMock(return_value="ok")
        await retry_async(mock, "arg1", key="val", max_attempts=1, initial_delay_s=0)
        mock.assert_awaited_once_with("arg1", key="val")

    async def test_retry_exhausted_chains_original_error(self):
        cause = TimeoutError("conn timeout")
        mock = AsyncMock(side_effect=cause)
        with pytest.raises(RetryExhaustedError) as exc_info:
            await retry_async(mock, max_attempts=2, initial_delay_s=0)
        assert exc_info.value.__cause__ is cause

    async def test_no_sleep_when_delay_zero(self, monkeypatch):
        slept = []
        async def fake_sleep(d):
            slept.append(d)
        monkeypatch.setattr("energybrain.utils.retry.asyncio.sleep", fake_sleep)
        mock = AsyncMock(side_effect=[RuntimeError("x"), 1])
        await retry_async(mock, max_attempts=2, initial_delay_s=0, backoff_factor=2.0)
        assert slept == [0]

    async def test_backoff_delay_increases(self, monkeypatch):
        slept = []
        async def fake_sleep(d):
            slept.append(d)
        monkeypatch.setattr("energybrain.utils.retry.asyncio.sleep", fake_sleep)
        mock = AsyncMock(side_effect=[RuntimeError(), RuntimeError(), RuntimeError(), 1])
        await retry_async(
            mock,
            max_attempts=4,
            initial_delay_s=1.0,
            backoff_factor=2.0,
            max_delay_s=100.0,
        )
        assert slept == [1.0, 2.0, 4.0]

    async def test_delay_capped_at_max(self, monkeypatch):
        slept = []
        async def fake_sleep(d):
            slept.append(d)
        monkeypatch.setattr("energybrain.utils.retry.asyncio.sleep", fake_sleep)
        mock = AsyncMock(side_effect=[RuntimeError(), RuntimeError(), 1])
        await retry_async(
            mock,
            max_attempts=3,
            initial_delay_s=10.0,
            backoff_factor=10.0,
            max_delay_s=15.0,
        )
        assert all(d <= 15.0 for d in slept)


class TestWithRetryDecorator:
    async def test_decorator_wraps_function(self):
        @with_retry(max_attempts=3, initial_delay_s=0)
        async def my_func(x: int) -> int:
            return x * 2

        result = await my_func(5)
        assert result == 10

    async def test_decorator_retries_on_failure(self):
        call_count = 0

        @with_retry(max_attempts=3, initial_delay_s=0)
        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("not yet")
            return "done"

        result = await flaky()
        assert result == "done"
        assert call_count == 3

    async def test_decorator_preserves_function_name(self):
        @with_retry()
        async def my_named_function():
            pass

        assert my_named_function.__name__ == "my_named_function"

    async def test_decorator_raises_after_exhaustion(self):
        @with_retry(max_attempts=2, initial_delay_s=0)
        async def always_fails():
            raise RuntimeError("boom")

        with pytest.raises(RetryExhaustedError):
            await always_fails()
