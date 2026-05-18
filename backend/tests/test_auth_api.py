"""
tests/test_auth_api.py
----------------------
Pytest test suite for the Ground Control Station FastAPI backend.

What is tested
~~~~~~~~~~~~~~
* Health check endpoint (unauthenticated, always accessible).
* JWT authentication — token issue, invalid credentials.
* Protected route access — correct 401 when no token is supplied.
* /api/me — profile endpoint returns correct username and role.
* Role-Based Access Control (RBAC):
    - Viewer can read telemetry (GET /api/status).
    - Viewer cannot call command routes (POST /api/reset → 403).
    - Commander can call all routes.

Why we mock the robot client
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
``robot_client.robot`` is a module-level singleton that makes real HTTP
requests to the Virtual Robot simulator.  The simulator runs as a separate
Docker container that will not be present during CI or local unit testing.

We use ``unittest.mock.patch`` to replace the relevant async methods with
``AsyncMock`` objects that return controlled fake responses.  This means:
  • Tests are fast (no network I/O).
  • Tests are deterministic (no chaos-monkey dropouts).
  • Docker does not need to be running.

Running the tests
~~~~~~~~~~~~~~~~~
From the ``backend/`` directory:

    pytest tests/ -v

Or with coverage:

    pytest tests/ -v --cov=. --cov-report=term-missing
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# Import the FastAPI application object.
# TestClient wraps it with an in-process ASGI transport so no real server
# needs to be started — requests are handled synchronously in the test process.
from main import app

# ---------------------------------------------------------------------------
# Test client fixture
# ---------------------------------------------------------------------------
# Using a module-scoped fixture means one TestClient is shared across all
# tests in this file, which is slightly faster than creating one per test
# while still being safe (TestClient is stateless between requests).


@pytest.fixture(scope="module")
def client():
    """Return a FastAPI TestClient wrapping the GCS app."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# Helper — obtain a JWT for a given demo user
# ---------------------------------------------------------------------------
# Centralising token retrieval avoids repeating the /token POST in every
# test that needs authentication.  If the /token endpoint changes, only
# this helper needs updating.

def get_token(client: TestClient, username: str, password: str) -> str:
    """POST /token and return the access_token string."""
    response = client.post(
        "/token",
        # /token uses application/x-www-form-urlencoded (OAuth2 password flow)
        data={"username": username, "password": password},
    )
    assert response.status_code == 200, (
        f"Token request failed ({response.status_code}): {response.text}"
    )
    return response.json()["access_token"]


def auth_headers(token: str) -> dict:
    """Return an Authorization header dict for the given Bearer token."""
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1. Health check
# ---------------------------------------------------------------------------

class TestHealth:
    """GET /health — always accessible, no authentication required."""

    def test_health_returns_200(self, client):
        response = client.get("/health")
        assert response.status_code == 200

    def test_health_returns_ok_body(self, client):
        response = client.get("/health")
        assert response.json() == {"status": "ok"}


# ---------------------------------------------------------------------------
# 2. Authentication — token endpoint
# ---------------------------------------------------------------------------

class TestTokenEndpoint:
    """POST /token — JWT issue and rejection."""

    def test_commander_login_returns_access_token(self, client):
        """Valid commander credentials → 200 with access_token field."""
        response = client.post(
            "/token",
            data={"username": "commander", "password": "commander123"},
        )
        assert response.status_code == 200
        body = response.json()
        assert "access_token" in body

    def test_commander_login_token_type_is_bearer(self, client):
        """token_type must be 'bearer' to comply with OAuth2 spec."""
        response = client.post(
            "/token",
            data={"username": "commander", "password": "commander123"},
        )
        assert response.json()["token_type"] == "bearer"

    def test_viewer_login_returns_access_token(self, client):
        """Valid viewer credentials → 200 with access_token field."""
        response = client.post(
            "/token",
            data={"username": "viewer", "password": "viewer123"},
        )
        assert response.status_code == 200
        assert "access_token" in response.json()

    def test_invalid_password_returns_401(self, client):
        """Wrong password → 401 Unauthorized."""
        response = client.post(
            "/token",
            data={"username": "commander", "password": "wrongpassword"},
        )
        assert response.status_code == 401

    def test_unknown_username_returns_401(self, client):
        """
        Non-existent username should still return generic 401.
        """
        response = client.post(
            "/token",
            data={"username": "ghost", "password": "anything"},
        )
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# 3. Protected route — unauthenticated access
# ---------------------------------------------------------------------------

class TestUnauthenticated:
    """
    Protected routes should return 401 without a token.
    """

    def test_status_without_token_returns_401(self, client):
        response = client.get("/api/status")
        assert response.status_code == 401

    def test_me_without_token_returns_401(self, client):
        response = client.get("/api/me")
        assert response.status_code == 401

    def test_move_without_token_returns_401(self, client):
        response = client.post("/api/move", json={"x": 5, "y": 5})
        assert response.status_code == 401

    def test_reset_without_token_returns_401(self, client):
        response = client.post("/api/reset")
        assert response.status_code == 401


# ---------------------------------------------------------------------------
# 4. /api/me — current user profile
# ---------------------------------------------------------------------------

class TestMeEndpoint:
    """GET /api/me — returns username and role for the token's owner."""

    def test_commander_me_returns_correct_username(self, client):
        token = get_token(client, "commander", "commander123")
        response = client.get("/api/me", headers=auth_headers(token))
        assert response.status_code == 200
        assert response.json()["username"] == "commander"

    def test_commander_me_returns_correct_role(self, client):
        token = get_token(client, "commander", "commander123")
        response = client.get("/api/me", headers=auth_headers(token))
        assert response.json()["role"] == "commander"

    def test_viewer_me_returns_correct_role(self, client):
        token = get_token(client, "viewer", "viewer123")
        response = client.get("/api/me", headers=auth_headers(token))
        assert response.status_code == 200
        assert response.json()["role"] == "viewer"

    def test_me_does_not_expose_password(self, client):
        """The password field must never appear in the /api/me response."""
        token = get_token(client, "commander", "commander123")
        response = client.get("/api/me", headers=auth_headers(token))
        assert "password" not in response.json()


# ---------------------------------------------------------------------------
# 5. GET /api/status — read-only telemetry (Viewer and Commander)
# ---------------------------------------------------------------------------

class TestStatusEndpoint:
    """GET /api/status — accessible to both roles; robot client is mocked."""

    # Fake payload that the mocked robot.get_status() will return
    FAKE_STATUS = {
        "id": "robot-sim-01",
        "status": "idle",
        "battery": 85.0,
        "position": {"x": 3, "y": 7},
    }

    def test_viewer_can_get_status(self, client):
        """Viewer token → 200 on GET /api/status."""
        token = get_token(client, "viewer", "viewer123")
        # Patch robot.get_status so no real simulator call is made
        with patch(
            "main.robot.get_status", new_callable=AsyncMock
        ) as mock_status:
            mock_status.return_value = self.FAKE_STATUS
            response = client.get("/api/status", headers=auth_headers(token))

        assert response.status_code == 200

    def test_status_returns_mocked_payload(self, client):
        """The mocked payload is passed through to the response unchanged."""
        token = get_token(client, "viewer", "viewer123")
        with patch(
            "main.robot.get_status", new_callable=AsyncMock
        ) as mock_status:
            mock_status.return_value = self.FAKE_STATUS
            response = client.get("/api/status", headers=auth_headers(token))

        body = response.json()
        assert body["id"] == "robot-sim-01"
        assert body["battery"] == 85.0
        assert body["position"] == {"x": 3, "y": 7}

    def test_commander_can_get_status(self, client):
        """Commander token → 200 on GET /api/status."""
        token = get_token(client, "commander", "commander123")
        with patch(
            "main.robot.get_status", new_callable=AsyncMock
        ) as mock_status:
            mock_status.return_value = self.FAKE_STATUS
            response = client.get("/api/status", headers=auth_headers(token))

        assert response.status_code == 200


# ---------------------------------------------------------------------------
# 6. POST /api/reset — Commander only
# ---------------------------------------------------------------------------

class TestResetEndpoint:
    """POST /api/reset — Commander only; Viewer receives 403."""

    FAKE_RESET_RESPONSE = {
        "success": True,
        "message": "Robot reset to home position.",
    }

    def test_viewer_reset_returns_403(self, client):
        """Viewer token should be blocked from reset."""
        token = get_token(client, "viewer", "viewer123")
        response = client.post("/api/reset", headers=auth_headers(token))
        assert response.status_code == 403

    def test_commander_reset_returns_200(self, client):
        """Commander token → 200 on POST /api/reset when robot is mocked."""
        token = get_token(client, "commander", "commander123")
        with patch(
            "main.robot.reset", new_callable=AsyncMock
        ) as mock_reset:
            mock_reset.return_value = self.FAKE_RESET_RESPONSE
            response = client.post("/api/reset", headers=auth_headers(token))

        assert response.status_code == 200

    def test_commander_reset_calls_robot_reset_once(self, client):
        """Verify the route actually calls robot.reset() exactly once."""
        token = get_token(client, "commander", "commander123")
        with patch(
            "main.robot.reset", new_callable=AsyncMock
        ) as mock_reset:
            mock_reset.return_value = self.FAKE_RESET_RESPONSE
            client.post("/api/reset", headers=auth_headers(token))

        mock_reset.assert_called_once()

    def test_commander_reset_returns_success_body(self, client):
        """Reset response body contains the mocked payload."""
        token = get_token(client, "commander", "commander123")
        with patch(
            "main.robot.reset", new_callable=AsyncMock
        ) as mock_reset:
            mock_reset.return_value = self.FAKE_RESET_RESPONSE
            response = client.post("/api/reset", headers=auth_headers(token))

        assert response.json()["success"] is True


# ---------------------------------------------------------------------------
# 7. POST /api/move — Commander only
# ---------------------------------------------------------------------------

class TestMoveEndpoint:
    """
    POST /api/move — Commander only endpoint.
    """

    FAKE_MOVE_RESPONSE = {
        "success": True,
        "message": "Robot moved.",
        "position": {"x": 5, "y": 10},
    }

    def test_viewer_move_returns_403(self, client):
        """Viewer token should be blocked from move."""
        token = get_token(client, "viewer", "viewer123")
        response = client.post(
            "/api/move",
            json={"x": 5, "y": 10},
            headers=auth_headers(token),
        )
        assert response.status_code == 403

    def test_commander_move_returns_200(self, client):
        """Commander token + valid coordinates → 200 when robot is mocked."""
        token = get_token(client, "commander", "commander123")
        with patch(
            "main.robot.move", new_callable=AsyncMock
        ) as mock_move:
            mock_move.return_value = self.FAKE_MOVE_RESPONSE
            response = client.post(
                "/api/move",
                json={"x": 5, "y": 10},
                headers=auth_headers(token),
            )

        assert response.status_code == 200

    def test_commander_move_calls_robot_move_with_correct_args(self, client):
        """Verify robot.move() receives the submitted coordinates."""
        token = get_token(client, "commander", "commander123")
        with patch(
            "main.robot.move", new_callable=AsyncMock
        ) as mock_move:
            mock_move.return_value = self.FAKE_MOVE_RESPONSE
            client.post(
                "/api/move",
                json={"x": 5, "y": 10},
                headers=auth_headers(token),
            )

        # robot.move() should receive the submitted integer coordinates.
        mock_move.assert_called_once_with(5, 10)

    def test_move_missing_body_returns_422(self, client):
        """Missing move body should return 422."""
        token = get_token(client, "commander", "commander123")
        response = client.post("/api/move", headers=auth_headers(token))
        assert response.status_code == 422
