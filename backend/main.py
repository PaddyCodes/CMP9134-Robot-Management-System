"""
Ground Control Station — FastAPI application entry point.
=========================================================

This module is the heart of the backend.  FastAPI reads this file when the
server starts and uses the `app` object defined here to handle every incoming
HTTP request.

Key concepts demonstrated in this file
---------------------------------------
* **FastAPI application factory** — how to create and configure an `app`.
* **CORS middleware** — what Cross-Origin Resource Sharing is and why it's
  needed when a browser frontend talks to a separate backend server.
* **Environment variables** — the standard way to inject runtime configuration
  (URLs, log levels, secrets) without hard-coding values.
* **Structured logging** — using Python's built-in `logging` module rather
  than plain `print()` calls, which is the industry standard.
* **Async route handlers** — why `async def` matters for I/O-bound work like
  HTTP calls to a robot API.
* **Error handling** — catching specific exceptions and returning meaningful
  responses instead of letting the server crash.
* **Bearer authentication** — every protected route requires the client to
  send a JWT in the ``Authorization: Bearer <token>`` header.  FastAPI
  extracts and validates the token automatically via the dependency declared
  in ``auth.py`` before the route handler is ever called.
* **JWT verification** — ``get_current_user`` in ``auth.py`` decodes the
  signed token, checks it has not expired or been tampered with, and returns
  the matching user dict.  If verification fails, FastAPI returns 401
  before the route handler runs.
* **Role-Based Access Control (RBAC)** — after authentication confirms *who*
  the user is, RBAC checks *what* they are allowed to do.  Commander-only
  routes inspect ``user["role"]`` and raise HTTP 403 Forbidden if a Viewer
  attempts to send a command.  Read-only routes (status, map, sensors) are
  accessible to both roles.
* **Audit logging** — every security-relevant action (login, RBAC denial,
  move command, reset command) is written to the application log in a
  consistent structured format::

      AUDIT action=<action> user=<username> role=<role> [extra fields]

  This provides an immutable trail of who did what and when, satisfying the
  mission logging requirement of the assessment brief without requiring a
  separate database table.  Timestamps are added automatically by the logging
  framework.  Passwords, tokens, and secrets are never logged.

Running the server locally
--------------------------
From the `backend/` directory:

    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Then visit http://localhost:8000/docs for the interactive API documentation
that FastAPI generates automatically from your code (no extra work required).
Use the "Authorize" button in Swagger UI to log in with a demo account and
test protected endpoints directly in the browser.
"""

import logging
import os

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel

from auth import authenticate_user, create_access_token, get_current_user
from robot_client import RobotConnectionError, robot

# ── Configuration from environment variables ───────────────────────────────
ROBOT_API_URL = os.getenv("ROBOT_API_URL", "http://localhost:5000")
LOG_LEVEL = os.getenv("LOG_LEVEL", "info")

# ── Logging setup ──────────────────────────────────────────────────────────
logging.basicConfig(level=LOG_LEVEL.upper())
logger = logging.getLogger(__name__)


# ── Audit log helper ───────────────────────────────────────────────────────
def audit(action: str, **fields) -> None:
    """Write a structured audit log entry using the module logger.

    All security-relevant events (logins, RBAC denials, robot commands) are
    routed through this helper so the log format stays consistent across the
    entire file.  The output format is::

        AUDIT action=<action> user=<username> role=<role> [key=value ...]

    Timestamps, severity, and module name are prepended automatically by the
    logging framework — no manual formatting is needed.

    Args:
        action: Short snake_case identifier for the event, e.g.
                ``"login_success"``, ``"move_attempt"``, ``"rbac_denied"``.
        **fields: Additional key/value pairs appended to the log line, e.g.
                  ``user="commander"``, ``x=5``, ``y=10``.

    Security note:
        Passwords, JWT tokens, and secret keys must never be passed as fields.
        This function is for audit metadata only.

    Severity convention:
        Callers choose the severity level by calling ``audit_warn`` for
        failure / denial events and ``audit`` (info) for normal events.
    """
    parts = [f"action={action}"] + [f"{k}={v}" for k, v in fields.items()]
    logger.info("AUDIT %s", " ".join(parts))


def audit_warn(action: str, **fields) -> None:
    """Write a structured audit log entry at WARNING level.

    Used for events that represent a potential security concern:
    failed logins and RBAC access denials.  WARNING-level entries
    stand out in log aggregators and are easier to alert on.

    See :func:`audit` for the log format and field conventions.
    """
    parts = [f"action={action}"] + [f"{k}={v}" for k, v in fields.items()]
    logger.warning("AUDIT %s", " ".join(parts))


# ── Request / response models ──────────────────────────────────────────────
class MoveCommand(BaseModel):
    """Request body for POST /api/move.

    Pydantic validates this automatically — if the client sends a float or
    a string where an int is expected, FastAPI returns a 422 Unprocessable
    Entity before the route handler is even called.
    """
    x: int
    y: int


# ── Application factory ────────────────────────────────────────────────────
app = FastAPI(
    title="Ground Control Station",
    description="CMP9134 — Robot Management System scaffold",
    version="0.1.0",
)

# ── CORS middleware ────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── RBAC helper ────────────────────────────────────────────────────────────
def require_commander(user: dict = Depends(get_current_user)) -> dict:
    """Dependency that enforces the 'commander' role.

    Role-Based Access Control (RBAC) separates authentication (proving who
    you are) from authorisation (deciding what you may do).  This helper is
    injected into command routes — move and reset — that must not be
    accessible to read-only Viewer accounts.

    Raises:
        HTTPException 403: If the authenticated user's role is not
            ``"commander"``.  An RBAC denial audit entry is written at
            WARNING level before raising.

    Returns:
        The user dict unchanged, so route handlers can log the username.
    """
    if user.get("role") != "commander":
        # Audit log: RBAC denial — WARNING level because this may indicate
        # a misconfigured client or an intentional privilege escalation attempt.
        audit_warn(
            "rbac_denied",
            user=user.get("username"),
            role=user.get("role"),
            attempted_operation="commander-only route",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Commander role required for this action",
        )
    return user


# ── Health check ───────────────────────────────────────────────────────────
@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


# ── Authentication — token issue ───────────────────────────────────────────
@app.post("/token")
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
) -> dict:
    """Issue a JWT access token in exchange for valid credentials.

    Accepts ``application/x-www-form-urlencoded`` with ``username`` and
    ``password`` fields — the standard OAuth2 Password flow.  The Swagger
    UI at ``/docs`` provides a built-in "Authorize" button that calls this
    endpoint automatically, making manual testing straightforward without
    a frontend.

    Returns a JSON object with ``access_token`` and ``token_type``.
    Raises HTTP 401 if the credentials do not match a known demo user.

    Audit events emitted:
        * ``login_success`` (INFO) — on valid credentials.
        * ``login_failed`` (WARNING) — on invalid credentials.  The password
          is never logged.
    """
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        # Audit log: failed login — WARNING because repeated failures may
        # indicate a brute-force or credential-stuffing attack.
        # The password is deliberately NOT included in the log entry.
        audit_warn(
            "login_failed",
            user=form_data.username,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = create_access_token({"sub": user["username"], "role": user["role"]})

    # Audit log: successful login — INFO level.
    # The token itself is not logged; only the identity metadata.
    audit(
        "login_success",
        user=user["username"],
        role=user["role"],
    )
    return {"access_token": token, "token_type": "bearer"}


# ── Current user profile ───────────────────────────────────────────────────
@app.get("/api/me")
async def get_me(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Return the profile of the currently authenticated user.

    Protected — requires a valid Bearer token in the Authorization header.
    The dashboard calls this on page load to determine the user's role and
    render the appropriate controls (Commander sees move/reset buttons;
    Viewer sees telemetry only).

    Returns ``username`` and ``role`` only — the password field is
    deliberately excluded from the response.
    """
    return {
        "username": current_user["username"],
        "role": current_user["role"],
    }


# ── Robot status proxy ─────────────────────────────────────────────────────
@app.get("/api/status")
async def get_status(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Return the current robot status (position, battery level, state).

    Protected — accessible to both Commander and Viewer roles (read-only).
    Proxies the request to the Virtual Robot API via ``robot_client.RobotClient``.
    Returns the robot's JSON payload directly, or an error dict if the robot
    simulator is unreachable.
    """
    try:
        return await robot.get_status()
    except RobotConnectionError as exc:
        logger.warning("Could not reach robot API: %s", exc)
        return {"error": str(exc)}


# ── Move command ───────────────────────────────────────────────────────────
@app.post("/api/move")
async def move_robot(
    command: MoveCommand,
    current_user: dict = Depends(require_commander),
) -> dict:
    """Send the robot to an absolute grid position (x, y).

    Protected — Commander role required.  Viewer accounts receive HTTP 403
    (enforced and logged by :func:`require_commander` before this handler runs).

    The request body must be JSON with integer fields ``x`` and ``y``.
    Pydantic validates the integer type automatically. Coordinate range
    validation will be tightened in a later validation/testing branch.

    Example request body::

        { "x": 5, "y": 10 }

    Returns the simulator's response (``success``, ``message``, new position)
    or a 503 JSON error if the robot is unreachable.

    Audit events emitted:
        * ``move_attempt`` (INFO) — immediately before the command is sent.
        * ``move_success`` (INFO) — after the simulator confirms the move.
    """
    # Audit log: record the intent before sending the command so that even
    # if the robot is unreachable the attempt is captured in the log trail.
    audit(
        "move_attempt",
        user=current_user["username"],
        role=current_user["role"],
        x=command.x,
        y=command.y,
    )
    try:
        result = await robot.move(command.x, command.y)

        # Audit log: command was accepted by the simulator.
        audit(
            "move_success",
            user=current_user["username"],
            role=current_user["role"],
            x=command.x,
            y=command.y,
        )
        return result

    except RobotConnectionError as exc:
        logger.warning(
            "Move command failed (x=%d y=%d, user: %s): %s",
            command.x, command.y, current_user["username"], exc,
        )
        raise HTTPException(status_code=503, detail=str(exc))


# ── Reset command ──────────────────────────────────────────────────────────
@app.post("/api/reset")
async def reset_robot(
    current_user: dict = Depends(require_commander),
) -> dict:
    """Reset the robot to its home position and clear its state.

    Protected — Commander role required.  Viewer accounts receive HTTP 403
    (enforced and logged by :func:`require_commander` before this handler runs).

    No request body is required.  Returns the simulator's response
    (``success``, ``message``) or a 503 JSON error if the robot is
    unreachable.

    Audit events emitted:
        * ``reset_attempt`` (INFO) — immediately before the command is sent.
        * ``reset_success`` (INFO) — after the simulator confirms the reset.
    """
    # Audit log: record the intent before sending the command.
    audit(
        "reset_attempt",
        user=current_user["username"],
        role=current_user["role"],
    )
    try:
        result = await robot.reset()

        # Audit log: simulator confirmed the reset.
        audit(
            "reset_success",
            user=current_user["username"],
            role=current_user["role"],
        )
        return result

    except RobotConnectionError as exc:
        logger.warning(
            "Reset command failed (user: %s): %s", current_user["username"], exc,
        )
        raise HTTPException(status_code=503, detail=str(exc))


# ── Map endpoint ───────────────────────────────────────────────────────────
@app.get("/api/map")
async def get_map(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Return the robot's current 2-D environment map.

    Protected — accessible to both Commander and Viewer roles (read-only).
    The simulator returns a grid of cells representing the traversable space,
    obstacles, and the robot's path history.  The dashboard uses this data
    to render the visual grid required by the assessment brief.

    Returns the simulator's map payload directly, or a 503 JSON error if
    the robot is unreachable.
    """
    try:
        result = await robot.get_map()
        logger.info(
            "Map data retrieved successfully (user: %s)",
            current_user["username"],
        )
        return result
    except RobotConnectionError as exc:
        logger.warning("Could not retrieve map: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))


# ── Sensor endpoint ────────────────────────────────────────────────────────
@app.get("/api/sensor")
async def get_sensors(
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Return the robot's current sensor readings.

    Protected — accessible to both Commander and Viewer roles (read-only).
    Returns distance and obstacle data for all cardinal directions, displayed
    as real-time telemetry on the dashboard alongside battery level and
    position.

    Returns the simulator's sensor payload directly, or a 503 JSON error if
    the robot is unreachable.
    """
    try:
        result = await robot.get_sensors()
        logger.info(
            "Sensor data retrieved successfully (user: %s)",
            current_user["username"],
        )
        return result
    except RobotConnectionError as exc:
        logger.warning("Could not retrieve sensor data: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))


# ── TODO: add your routes below ────────────────────────────────────────────
#
# @app.websocket("/ws/telemetry")
# async def ws_telemetry(websocket: WebSocket):
#     """Stream live sensor data to a connected browser client."""
#     await websocket.accept()
#     try:
#         while True:
#             data = await robot.get_status()
#             await websocket.send_json(data)
#             await asyncio.sleep(0.5)
#     except WebSocketDisconnect:
#         logger.info("Telemetry client disconnected")
