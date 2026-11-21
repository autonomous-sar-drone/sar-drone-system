"""Dedicated operator-input WebSocket; MissionService owns flight authority.

The socket exchanges four-axis intent, never MAVLink messages. One active
socket gets one opaque control epoch. A closed connection or sustained input
silence revokes authority and requests BRAKE. Brief input gaps only request
neutral velocity in GUIDED; reconnects cannot replay old key presses.
"""

import asyncio
import json
import logging
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.mission.errors import MissionExecutionError
from app.mission.mission_service import MissionService


logger = logging.getLogger(__name__)
router = APIRouter()


async def _watch_manual_session(service: MissionService, epoch: str) -> None:
    while True:
        await asyncio.sleep(0.1)
        if not await service.manual_watchdog_tick(epoch):
            return


@router.websocket("/ws/manual-control")
async def manual_control_websocket(websocket: WebSocket) -> None:
    # Browsers include Origin. Reject unrelated websites attempting to claim
    # control of a localhost simulator via cross-site WebSocket requests.
    origin = websocket.headers.get("origin")
    if origin is not None:
        parsed = urlsplit(origin)
        host = urlsplit("http://" + websocket.headers.get("host", "")).hostname
        if (parsed.scheme not in ("http", "https") or
                parsed.hostname is None or
                (parsed.hostname != host and not (
                    parsed.hostname in ("localhost", "127.0.0.1") and
                    host in ("localhost", "127.0.0.1")
                ))):
            await websocket.close(code=4403)
            return
    await websocket.accept()
    service: MissionService = websocket.app.state.mission_service
    try:
        ready = await service.open_manual_channel()
    except MissionExecutionError:
        # No manual-ready frame can be issued before confirmed vehicle handoff.
        await websocket.close(code=4403)
        return

    epoch = str(ready["controlEpoch"])
    receive_task: asyncio.Task[str] | None = None
    watchdog_task: asyncio.Task[None] | None = None
    try:
        await websocket.send_json({"type": "manual_ready", "data": ready})
        receive_task = asyncio.create_task(websocket.receive_text())
        watchdog_task = asyncio.create_task(_watch_manual_session(service, epoch))
        while True:
            done, _ = await asyncio.wait(
                {receive_task, watchdog_task}, return_when=asyncio.FIRST_COMPLETED,
            )
            if watchdog_task in done:
                # Prolonged silence, vehicle-mode change, or an HTTP handoff
                # invalidated this epoch; do not accept any further frames.
                if watchdog_task.exception() is not None:
                    error = watchdog_task.exception()
                    logger.error("Manual watchdog failed", exc_info=(type(error), error, error.__traceback__))
                await websocket.close(code=1011)
                break

            assert receive_task in done
            try:
                raw = receive_task.result()
            except WebSocketDisconnect:
                break

            try:
                # The frontend sends ~170-byte packets. A giant control frame
                # cannot be trusted and should be neutralized, not processed.
                wire: Any = json.loads(raw) if len(raw) <= 2048 else None
            except (json.JSONDecodeError, TypeError):
                wire = None
            data = wire.get("data") if isinstance(wire, dict) and wire.get("type") == "manual_input" else None
            if not isinstance(data, dict):
                data = {}
            try:
                result = await service.manual_channel_input(epoch, data)
            except MissionExecutionError:
                await websocket.close(code=1011)
                break
            if result.get("revoked"):
                await websocket.close(code=4403)
                break
            await websocket.send_json({
                "type": "manual_ack",
                "data": {"sequence": result["sequence"], "accepted": result["accepted"]},
            })
            clear_code = result.get("clearFeedback")
            if clear_code:
                await websocket.send_json({
                    "type": "manual_feedback", "data": {"code": clear_code, "active": False},
                })
            feedback = result.get("feedback")
            if feedback:
                await websocket.send_json({
                    "type": "manual_feedback", "data": {"code": feedback, "active": True},
                })
            receive_task = asyncio.create_task(websocket.receive_text())
    except WebSocketDisconnect:
        pass
    finally:
        for task in (receive_task, watchdog_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (receive_task, watchdog_task) if task is not None),
            return_exceptions=True,
        )
        await service.close_manual_channel(epoch)
