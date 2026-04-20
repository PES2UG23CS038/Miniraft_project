import os
import asyncio
import logging
from typing import List

from fastapi import FastAPI

from shared.message_types import (
    AppendEntriesRequest,
    AppendEntriesResponse,
    HealthResponse,
    SubmitStrokeRequest,
    SyncLogRequest,
    SyncLogResponse,
    VoteRequest,
    VoteResponse,
)
from shared.raft_node import RaftNode

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

app = FastAPI(title="Mini RAFT Replica")

REPLICA_ID = os.getenv("REPLICA_ID", "replica1")
RPC_HOST = os.getenv("RPC_HOST", "0.0.0.0")
RPC_PORT = int(os.getenv("RPC_PORT", "5000"))
PEERS = os.getenv("PEERS", "").split()

node = RaftNode(
    node_id=REPLICA_ID,
    host=RPC_HOST,
    port=RPC_PORT,
    peers=PEERS,
)


@app.on_event("startup")
async def startup_event() -> None:
    asyncio.create_task(node.start())


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await node.stop()


@app.post("/request-vote", response_model=VoteResponse)
async def request_vote(req: VoteRequest) -> VoteResponse:
    return await node.handle_request_vote(req)


@app.post("/append-entries", response_model=AppendEntriesResponse)
async def append_entries(req: AppendEntriesRequest) -> AppendEntriesResponse:
    return await node.handle_append_entries(req)


@app.post("/sync-log")
async def sync_log(req: SyncLogRequest):
    return await node.handle_sync_log(req)


@app.post("/submit-stroke")
async def submit_stroke(req: SubmitStrokeRequest) -> dict:
    return await node.handle_submit_stroke(req)


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return await node.snapshot()


@app.get("/log", response_model=List[dict])
async def committed_log() -> List[dict]:
    return await node.committed_log()


@app.get("/")
async def root() -> dict:
    return {"id": REPLICA_ID, "term": node.current_term, "state": node.state}