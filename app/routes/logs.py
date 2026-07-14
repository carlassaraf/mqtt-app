from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.db import get_recent_logs
from app.mqtt_client import register_ws_queue, unregister_ws_queue

router = APIRouter(tags=["logs"])


@router.get("/api/logs")
def get_logs(limit: int = 200):
    return get_recent_logs(limit)


@router.websocket("/ws/logs")
async def logs_ws(ws: WebSocket):
    """Live tail of the subscribed log topic. Sends each new message as JSON
    the instant mqtt_client receives it -- no polling."""
    await ws.accept()
    queue = register_ws_queue()
    try:
        while True:
            msg = await queue.get()
            await ws.send_json(msg)
    except WebSocketDisconnect:
        pass
    finally:
        unregister_ws_queue(queue)
