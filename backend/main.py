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

Running the server locally
--------------------------
From the `backend/` directory:

    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

Then visit http://localhost:8000/docs for the interactive API documentation
that FastAPI generates automatically from your code (no extra work required).
"""

import logging
import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from robot_client import robot, RobotConnectionError

# ── Configuration from environment variables ───────────────────────────────
ROBOT_API_URL = os.getenv("ROBOT_API_URL", "http://localhost:5000")
LOG_LEVEL = os.getenv("LOG_LEVEL", "info")

# ── Logging setup ──────────────────────────────────────────────────────────
logging.basicConfig(level=LOG_LEVEL.upper())
logger = logging.getLogger(__name__)


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


# ── Health check ───────────────────────────────────────────────────────────
@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


# ── Robot status proxy ─────────────────────────────────────────────────────
@app.get("/api/status")
async def get_status():
    """Return the current robot status (position, battery level, state).

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
async def move_robot(command: MoveCommand):
    """Send the robot to an absolute grid position (x, y).

    The request body must be JSON with integer fields ``x`` and ``y``.
    Pydantic validates the integer type automatically. Coordinate range
    validation will be tightened in a later validation/testing branch.

    Example request body::

        { "x": 5, "y": 10 }

    Returns the simulator's response (``success``, ``message``, new position)
    or a 503 JSON error if the robot is unreachable.
    """
    try:
        result = await robot.move(command.x, command.y)
        logger.info("Move command succeeded: x=%d y=%d", command.x, command.y)
        return result
    except RobotConnectionError as exc:
        logger.warning("Move command failed (x=%d y=%d): %s", command.x, command.y, exc)
        raise HTTPException(status_code=503, detail=str(exc))


# ── Reset command ──────────────────────────────────────────────────────────
@app.post("/api/reset")
async def reset_robot():
    """Reset the robot to its home position and clear its state.

    No request body is required.  Returns the simulator's response
    (``success``, ``message``) or a 503 JSON error if the robot is
    unreachable.
    """
    try:
        result = await robot.reset()
        logger.info("Reset command succeeded")
        return result
    except RobotConnectionError as exc:
        logger.warning("Reset command failed: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))


# ── Map endpoint ───────────────────────────────────────────────────────────
@app.get("/api/map")
async def get_map():
    """Return the robot's current 2-D environment map.

    The simulator returns a grid of cells representing the traversable space,
    obstacles, and the robot's path history.  The dashboard uses this data
    to render the visual grid required by the assessment brief.

    Returns the simulator's map payload directly, or a 503 JSON error if
    the robot is unreachable.
    """
    try:
        result = await robot.get_map()
        logger.info("Map data retrieved successfully")
        return result
    except RobotConnectionError as exc:
        logger.warning("Could not retrieve map: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))


# ── Sensor endpoint ───────────────────────────────────────────────────────
@app.get("/api/sensor")
async def get_sensors():
    """Return the robot's current sensor readings.

    Returns distance and obstacle data for all cardinal directions, displayed
    as real-time telemetry on the dashboard alongside battery level and
    position.

    Returns the simulator's sensor payload directly, or a 503 JSON error if
    the robot is unreachable.
    """
    try:
        result = await robot.get_sensors()
        logger.info("Sensor data retrieved successfully")
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
