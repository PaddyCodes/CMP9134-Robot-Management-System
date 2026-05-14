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

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# 1. ADD THIS IMPORT NEAR THE TOP:
# This pulls the 'router' variable from the file you just created
from legacy_stats import router as legacy_stats_router

from robot_client import robot, RobotConnectionError

# ── Configuration from environment variables ───────────────────────────────
# os.getenv(key, default) reads a value from the process environment.
# If the variable is not set, the second argument is used as a fallback.
#
# Why environment variables instead of hard-coded strings?
#   • Different environments (dev / staging / production) can use different
#     values without changing source code.
#   • Secrets (API keys, passwords) are never committed to version control.
#   • The values can be overridden in docker-compose.yml or a .env file
#     without touching this file.
ROBOT_API_URL = os.getenv("ROBOT_API_URL", "http://localhost:5000")
LOG_LEVEL = os.getenv("LOG_LEVEL", "info")

# ── Logging setup ──────────────────────────────────────────────────────────
# basicConfig() configures the root logger once at startup.
# LOG_LEVEL.upper() converts e.g. "info" → "INFO" to match the constants
# expected by the logging module (logging.INFO, logging.DEBUG, etc.).
#
# Use `logger.info(...)`, `logger.warning(...)`, `logger.error(...)` etc.
# throughout your code instead of print().  Benefits:
#   • Timestamps, severity levels, and module names are added automatically.
#   • Output can be redirected to files or log aggregators without code changes.
#   • Verbosity is controlled at runtime via LOG_LEVEL, not by editing code.
logging.basicConfig(level=LOG_LEVEL.upper())
logger = logging.getLogger(__name__)
# __name__ is the module's fully-qualified name (e.g. "main").
# Using __name__ means log messages identify which module produced them.


# ── Application factory ────────────────────────────────────────────────────
# FastAPI() creates the ASGI application object.  Everything—routes,
# middleware, startup hooks—is registered on this object.
#
# The metadata arguments (title, description, version) appear in:
#   • The auto-generated /docs (Swagger UI) page.
#   • The /openapi.json schema that clients can use to generate SDK code.
app = FastAPI(
    title="Ground Control Station",
    description="CMP9134 — Robot Management System scaffold",
    version="0.1.0",
)

# ── CORS middleware ────────────────────────────────────────────────────────
# Browsers enforce the Same-Origin Policy: a web page served from one origin
# (e.g. http://localhost:3000) is NOT allowed to make fetch() requests to a
# different origin (e.g. http://localhost:8000) unless that second origin
# explicitly opts in via CORS headers.
#
# CORSMiddleware adds the necessary HTTP response headers so the browser
# permits the cross-origin request.
#
# allow_origins=["*"]
#   Permit requests from ANY origin.  Fine for development or a controlled
#   lab network, but in production you should list specific allowed origins
#   (e.g. ["https://yourapp.example.com"]) to prevent abuse.
#
# allow_credentials=True
#   Allow cookies and HTTP authentication headers to be sent cross-origin.
#   Note: this cannot be combined with allow_origins=["*"] in a real
#   production deployment — the browser will reject it.
#
# allow_methods=["*"], allow_headers=["*"]
#   Don't restrict which HTTP verbs or request headers are allowed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 2. ADD THIS LINE ANYWHERE AFTER 'app = FastAPI(...)'
# This physically attaches the bad code to your live server
app.include_router(legacy_stats_router)


# ── Health check ───────────────────────────────────────────────────────────
# A health check endpoint is a simple route that returns 200 OK when the
# server is running correctly.  It is called by:
#   • Docker — via the HEALTHCHECK instruction in the Dockerfile, which marks
#     the container "healthy" once this endpoint responds successfully.
#   • The CI pipeline — the compose-test job polls this URL before running
#     further tests (see .github/workflows/ci.yml).
#   • Load balancers — to decide whether to route traffic to this instance.
#
# include_in_schema=False hides this internal route from the /docs page
# because it is infrastructure plumbing, not part of the public API.
#
# Note: this is a plain `def` (synchronous), not `async def`.  That is fine
# here because the function does no I/O — it just returns a dict immediately.
# FastAPI handles both sync and async handlers correctly.
@app.get("/health", include_in_schema=False)
def health():
    return {"status": "ok"}


# ── Robot status proxy ─────────────────────────────────────────────────────
# TODO: add authentication, RBAC, and mission logging around these endpoints.
#
# This route is `async def` because it calls `await robot.get_status()`,
# which performs a network request to the robot simulator.
#
# Why async matters for network I/O
# ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
# FastAPI uses an async event loop (from the `asyncio` standard library).
# When an async handler reaches an `await`, it *suspends itself* and lets the
# event loop handle other incoming requests, rather than blocking the whole
# process.  This means one Python process can serve many concurrent requests
# without needing a thread per request — crucial for a real-time robotics
# dashboard where many clients may be connected simultaneously.
#
# Error handling pattern
# ~~~~~~~~~~~~~~~~~~~~~~
# We catch `RobotConnectionError` specifically — not bare `Exception`.
# Catching only the errors we expect means unexpected bugs (e.g. a TypeError
# from bad data) still propagate and cause a proper 500 response, rather than
# being silently swallowed by an overly broad except clause.
#
# Returning {"error": str(exc)} gives the frontend a machine-readable message
# instead of letting FastAPI produce an unhelpful 500 HTML page.
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
        # Log the error server-side at WARNING level so it appears in the
        # server logs without spamming at ERROR level for expected downtime.
        logger.warning("Could not reach robot API: %s", exc)
        return {"error": str(exc)}


# ── TODO: add your routes below ────────────────────────────────────────────
# Use the skeletons below as starting points.  Each route should:
#   1. Validate inputs — FastAPI does this automatically when you add type
#      hints to the function parameters (e.g. `x: int`).
#   2. Call the appropriate RobotClient method from robot_client.py.
#   3. Handle RobotConnectionError (and any other expected errors) gracefully.
#   4. Return a meaningful JSON response.
#
# @app.post("/api/move")
# async def move(x: int, y: int):
#     """Send the robot to position (x, y)."""
#     try:
#         return await robot.move(x, y)
#     except RobotConnectionError as exc:
#         logger.warning("Move command failed: %s", exc)
#         return {"error": str(exc)}
#
# @app.websocket("/ws/telemetry")
# async def ws_telemetry(websocket: WebSocket):
#     """Stream live sensor data to a connected browser client.
#
#     WebSockets maintain a persistent two-way connection, making them ideal
#     for low-latency telemetry feeds (position, battery, sensor readings).
#     Unlike HTTP, you don't need to poll — the server pushes updates.
#     """
#     await websocket.accept()
#     try:
#         while True:
#             data = await robot.get_status()
#             await websocket.send_json(data)
#             await asyncio.sleep(0.5)   # push an update every 500 ms
#     except WebSocketDisconnect:
#         logger.info("Telemetry client disconnected")
