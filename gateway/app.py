import asyncio
import json
import logging
import os
from typing import Dict, List, Optional, Set

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

GATEWAY_HOST = os.getenv("GATEWAY_HOST", "0.0.0.0")
GATEWAY_PORT = int(os.getenv("GATEWAY_PORT", "8080"))
INITIAL_LEADER = os.getenv("INITIAL_LEADER", "http://replica1:5000")
CANDIDATES = os.getenv(
    "CANDIDATES",
    "http://replica1:5000 http://replica2:5001 http://replica3:5002"
).split()
FRONTEND_DIR = os.getenv("FRONTEND_DIR", "/app/frontend")

app = FastAPI(title="Gateway")
clients: Set[WebSocket] = set()
leader_url: Optional[str] = INITIAL_LEADER
client = httpx.AsyncClient(timeout=5.0)


# ---------------- LEADER DISCOVERY ----------------
async def pick_leader() -> Optional[str]:
    global leader_url
    for candidate in ([leader_url] if leader_url else []) + CANDIDATES:
        if not candidate:
            continue
        try:
            resp = await client.get(f"{candidate}/health")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("state") == "leader":
                    leader_url = candidate
                    return leader_url
                if data.get("leader"):
                    leader_url = data["leader"]
                    return leader_url
        except Exception:
            continue
    return None


# ---------------- FETCH LOG ----------------
async def fetch_committed_log() -> List[Dict]:
    url = await pick_leader()
    if not url:
        return []
    try:
        resp = await client.get(f"{url}/log")
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("Failed to fetch committed log: %s", exc)
        return []


# ---------------- SUBMIT STROKE ----------------
async def submit_stroke(stroke: Dict) -> bool:
    global leader_url
    url = await pick_leader()
    if not url:
        return False
    try:
        resp = await client.post(f"{url}/submit-stroke", json={"stroke": stroke})
        resp.raise_for_status()
        data = resp.json()

        if data.get("accepted"):
            return True

        # leader redirect hint
        if data.get("leader"):
            leader_url = data["leader"]

        return False
    except Exception as exc:
        logger.warning("submit_stroke failed: %s", exc)
        return False


# ---------------- BROADCAST ----------------
async def broadcast(message: Dict) -> None:
    dead: List[WebSocket] = []
    payload = json.dumps(message)

    for ws in clients:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)

    for ws in dead:
        clients.discard(ws)


# ---------------- SYNC LOOP (FIX) ----------------
last_log: List[Dict] = []

async def sync_loop():
    global last_log
    await asyncio.sleep(2)  # wait for system startup

    while True:
        try:
            log = await fetch_committed_log()

            if len(log) > len(last_log):
                new_entries = log[len(last_log):]

                for stroke in new_entries:
                    await broadcast({"type": "stroke", "stroke": stroke})

                last_log = log

        except Exception as exc:
            logger.warning("Sync loop error: %s", exc)

        await asyncio.sleep(0.5)  # fast sync


# ---------------- WEBSOCKET ----------------
@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await ws.accept()
    clients.add(ws)

    history = await fetch_committed_log()
    await ws.send_text(json.dumps({"type": "history", "strokes": history}))

    try:
        while True:
            data = await ws.receive_json()

            if data.get("type") == "stroke":
                stroke = data.get("stroke", {})
                await submit_stroke(stroke)

    except WebSocketDisconnect:
        clients.discard(ws)

    except Exception as exc:
        logger.warning("WebSocket error: %s", exc)
        clients.discard(ws)


# ---------------- FRONTEND ----------------
@app.get("/")
async def root() -> HTMLResponse:
    try:
        with open(os.path.join(FRONTEND_DIR, "index.html"), "r", encoding="utf-8") as fp:
            html = fp.read()
    except FileNotFoundError:
        html = "<h1>Gateway running</h1>"

    return HTMLResponse(content=html)


# ---------------- STARTUP / SHUTDOWN ----------------
@app.on_event("startup")
async def startup_event():
    asyncio.create_task(sync_loop())


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await client.aclose()