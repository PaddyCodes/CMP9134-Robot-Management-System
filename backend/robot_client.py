"""
Robot API client scaffold.

Provides a small async wrapper around the Virtual Robot REST API.
Extended with retry logic, timeout handling, and additional endpoints:
move(), reset(), get_map(), get_sensors().

Design patterns
~~~~~~~~~~~~~~~
* Facade    – clean public methods hide all HTTP mechanics and error
              translation from the rest of the application.
* Singleton – the module-level ``robot`` instance is shared across the
              entire FastAPI process (see bottom of file).
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import httpx

ROBOT_API_URL = os.getenv("ROBOT_API_URL", "http://localhost:5000")

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry configuration
# ---------------------------------------------------------------------------
_MAX_RETRIES: int = 3       # total attempts (1 initial + 2 retries)
_BASE_BACKOFF: float = 0.5  # seconds before first retry; doubles each attempt
_TIMEOUT: float = 5.0       # per-request timeout in seconds


class RobotConnectionError(Exception):
    """Raised when a request to the robot API fails."""


class RobotClient:
    """Async HTTP client for the Virtual Robot API.

    All public methods share the same ``_request`` helper which provides
    uniform timeout handling, exponential-backoff retry, and
    ``RobotConnectionError`` translation.
    """

    def __init__(self, base_url: str = ROBOT_API_URL) -> None:
        self._base = base_url.rstrip("/")

    # ------------------------------------------------------------------
    # Internal helper – single place for retry / error logic
    # ------------------------------------------------------------------
    async def _request(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Send an HTTP request with exponential-backoff retry.

        Args:
            method: HTTP verb (``"GET"``, ``"POST"``, …).
            path:   Path relative to the robot base URL (e.g. ``"/api/move"``).
            **kwargs: Forwarded verbatim to ``httpx.AsyncClient.request``.

        Returns:
            Parsed JSON response body as a Python dict.

        Raises:
            RobotConnectionError: After all retry attempts are exhausted, or
                immediately on a definitive 4xx HTTP error (retrying would
                not help, e.g. 422 Validation Error for bad coordinates).
        """
        last_exc: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.request(
                        method,
                        f"{self._base}{path}",
                        timeout=_TIMEOUT,
                        **kwargs,
                    )
                    response.raise_for_status()
                    return response.json()

            except httpx.TimeoutException as exc:
                # Transient – the chaos monkey may have introduced latency.
                last_exc = exc
                logger.warning(
                    "Robot API timeout [%s %s] attempt %d/%d",
                    method, path, attempt, _MAX_RETRIES,
                )

            except httpx.HTTPStatusError as exc:
                # 4xx responses are definitive; retrying will not help.
                logger.error(
                    "Robot API HTTP %s [%s %s]: %s",
                    exc.response.status_code, method, path, exc.response.text,
                )
                raise RobotConnectionError(
                    f"Robot API returned {exc.response.status_code}: "
                    f"{exc.response.text}"
                ) from exc

            except httpx.RequestError as exc:
                # Network-level failure (connection refused, DNS, etc.)
                last_exc = exc
                logger.warning(
                    "Robot API connection error [%s %s] attempt %d/%d: %s",
                    method, path, attempt, _MAX_RETRIES, exc,
                )

            # Exponential backoff before next attempt (skip after final try)
            if attempt < _MAX_RETRIES:
                backoff = _BASE_BACKOFF * (2 ** (attempt - 1))
                logger.debug("Retrying in %.1fs…", backoff)
                await asyncio.sleep(backoff)

        raise RobotConnectionError(
            f"Robot API unreachable after {_MAX_RETRIES} attempts "
            f"[{method} {path}]: {last_exc}"
        ) from last_exc

    # ------------------------------------------------------------------
    # Public API methods
    # ------------------------------------------------------------------
    async def get_status(self) -> dict[str, Any]:
        """Fetch current robot status (position, battery, state)."""
        return await self._request("GET", "/api/status")

    async def move(self, x: int, y: int) -> dict[str, Any]:
        """Send a move command to the robot.

        Args:
            x: Target column on the grid (integer, 0–20).
            y: Target row    on the grid (integer, 0–20).

        Returns:
            API response dict containing ``success``, ``message``, and the
            robot's new position.

        Raises:
            RobotConnectionError: On network failure, timeout, or if the
                simulator rejects the coordinates (e.g. out of range).

        Note:
            Coordinates are explicitly cast to ``int`` before sending.
            The simulator's /api/move endpoint requires integer values;
            passing floats causes a 422 Validation Error.
        """
        payload = {"x": int(x), "y": int(y)}
        logger.info("Sending move command: %s", payload)
        return await self._request("POST", "/api/move", json=payload)

    async def reset(self) -> dict[str, Any]:
        """Reset the robot simulation to its home position.

        Returns:
            API response dict containing ``success`` and ``message``.

        Raises:
            RobotConnectionError: If the simulator is unreachable.
        """
        logger.info("Sending reset command")
        return await self._request("POST", "/api/reset")

    async def get_map(self) -> dict[str, Any]:
        """Retrieve the robot's current 2-D environment map.

        The simulator returns a grid of cells used by the dashboard to
        render the visual map required by the assessment brief.

        Returns:
            API response dict containing the map grid and its dimensions.

        Raises:
            RobotConnectionError: If the simulator is unreachable.
        """
        return await self._request("GET", "/api/map")

    async def get_sensors(self) -> dict[str, Any]:
        """Retrieve the robot's current sensor readings.

        Returns distance/obstacle data for all cardinal directions,
        displayed as real-time telemetry on the dashboard.

        Returns:
            API response dict containing per-direction sensor values.

        Raises:
            RobotConnectionError: If the simulator is unreachable.
        """
        return await self._request("GET", "/api/sensor")


# Module-level singleton used by main.py
robot = RobotClient()