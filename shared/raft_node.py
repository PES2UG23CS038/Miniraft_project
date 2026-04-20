from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from .message_types import (
    AppendEntriesRequest,
    AppendEntriesResponse,
    HealthResponse,
    LogEntryModel,
    SubmitStrokeRequest,
    SyncLogResponse,
    VoteRequest,
    VoteResponse,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


@dataclass
class LogEntry:
    index: int
    term: int
    payload: Dict[str, Any]


class RaftNode:
    def __init__(
        self,
        node_id: str,
        host: str,
        port: int,
        peers: List[str],
        heartbeat_interval: float = 0.1,
        election_timeout_range: tuple[float, float] = (1.0, 1.5),
    ) -> None:
        self.id = node_id
        self.host = host
        self.port = port
        self.peers = peers

        self.state: str = "follower"
        self.current_term: int = 0
        self.voted_for: Optional[str] = None

        self.log: List[LogEntry] = []
        self.commit_index: int = 0

        self.leader_id: Optional[str] = None

        self.next_index: Dict[str, int] = {}
        self.match_index: Dict[str, int] = {}

        self._heartbeat_interval = heartbeat_interval
        self._election_timeout_range = election_timeout_range

        self._last_heartbeat = time.monotonic()
        self._stop_event = asyncio.Event()
        self._tasks: List[asyncio.Task] = []
        self._client: Optional[httpx.AsyncClient] = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        self._client = httpx.AsyncClient(timeout=0.5)
        self.state = "follower"
        self.voted_for = None
        self._last_heartbeat = time.monotonic()

        self._tasks.append(asyncio.create_task(self._run_election_timer()))
        logger.info("%s started", self.id)

    async def stop(self) -> None:
        self._stop_event.set()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._client:
            await self._client.aclose()

    def _majority(self) -> int:
        return (len(self.peers) + 1) // 2 + 1

    def _last_log_index_term(self):
        if not self.log:
            return 0, 0
        e = self.log[-1]
        return e.index, e.term

    async def _run_election_timer(self):
        while not self._stop_event.is_set():
            await asyncio.sleep(0.05)
            if self.state == "leader":
                continue

            elapsed = time.monotonic() - self._last_heartbeat
            timeout = random.uniform(*self._election_timeout_range)

            if elapsed >= timeout:
                await self._start_election()

    async def _start_election(self):
        async with self._lock:
            self.state = "candidate"
            self.current_term += 1
            self.voted_for = self.id
            self.leader_id = None
            self._last_heartbeat = time.monotonic()

            votes = 1
            term = self.current_term
            last_index, last_term = self._last_log_index_term()

            logger.info("%s starting election term %s", self.id, term)

        responses = await asyncio.gather(
            *[self._send_vote_request(p, term, last_index, last_term) for p in self.peers],
            return_exceptions=True,
        )

        for res in responses:
            if isinstance(res, VoteResponse) and res.vote_granted and res.term == term:
                votes += 1
            elif isinstance(res, VoteResponse) and res.term > term:
                async with self._lock:
                    self.current_term = res.term
                    self.state = "follower"
                    self.voted_for = None
                return

        async with self._lock:
            if self.state != "candidate":
                return

        if votes >= self._majority():
            await self._become_leader()
        else:
            async with self._lock:
                self.state = "follower"
                self.voted_for = None

    async def _become_leader(self):
        async with self._lock:
            self.state = "leader"
            self.leader_id = self.id

            last_index, _ = self._last_log_index_term()
            self.next_index = {p: last_index + 1 for p in self.peers}
            self.match_index = {p: 0 for p in self.peers}

        logger.info("%s became LEADER term %s", self.id, self.current_term)
        self._tasks.append(asyncio.create_task(self._heartbeat_loop()))

    async def _heartbeat_loop(self):
        while not self._stop_event.is_set():
            if self.state != "leader":
                return
            await self.replicate([])
            await asyncio.sleep(self._heartbeat_interval)

    async def _send_vote_request(self, peer, term, last_index, last_term):
        try:
            resp = await self._client.post(
                f"{peer}/request-vote",
                json=VoteRequest(
                    term=term,
                    candidate_id=self.id,
                    last_log_index=last_index,
                    last_log_term=last_term,
                ).model_dump(),
            )
            return VoteResponse(**resp.json())
        except Exception:
            return None

    async def replicate(self, entries: Optional[List[LogEntry]] = None):
        if self.state != "leader":
            return

        entries = entries or []

        tasks = []
        for peer in self.peers:
            prev_index = self.next_index.get(peer, 1) - 1
            prev_term = self.log[prev_index - 1].term if prev_index > 0 else 0

            payload_entries = [
                LogEntryModel(index=e.index, term=e.term, payload=e.payload).model_dump()
                for e in entries
            ]

            req = AppendEntriesRequest(
                term=self.current_term,
                leader_id=self.id,
                prev_log_index=prev_index,
                prev_log_term=prev_term,
                entries=payload_entries,
                leader_commit=self.commit_index,
            )

            tasks.append(self._send_append_entries(peer, req))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        for peer, res in zip(self.peers, results):
            if not isinstance(res, AppendEntriesResponse):
                continue

            if res.term > self.current_term:
                async with self._lock:
                    self.current_term = res.term
                    self.state = "follower"
                    self.voted_for = None
                return

            if res.success:
                self.match_index[peer] = res.match_index
                self.next_index[peer] = res.match_index + 1
            else:
                # follower is lagging — push all missing committed entries
                logger.info("%s rejected AppendEntries from %s at log len %s, triggering sync", peer, self.id, res.follower_len)
                asyncio.create_task(self._push_sync_to_follower(peer, res.follower_len))

        await self._advance_commit_index()

    async def _advance_commit_index(self):
        match_indexes = list(self.match_index.values()) + [len(self.log)]
        match_indexes.sort(reverse=True)
        quorum_index = match_indexes[self._majority() - 1]

        if quorum_index > self.commit_index and self.log[quorum_index - 1].term == self.current_term:
            self.commit_index = quorum_index
            logger.info("%s commit_index -> %s", self.id, self.commit_index)

    async def _send_append_entries(self, peer, req):
        try:
            resp = await self._client.post(f"{peer}/append-entries", json=req.model_dump())
            return AppendEntriesResponse(**resp.json())
        except Exception:
            return None

    async def handle_request_vote(self, req: VoteRequest):
        async with self._lock:
            if req.term < self.current_term:
                return VoteResponse(term=self.current_term, vote_granted=False, voter_id=self.id)

            if req.term > self.current_term:
                self.current_term = req.term
                self.voted_for = None
                self.state = "follower"

            up_to_date = (req.last_log_term, req.last_log_index) >= self._last_log_index_term()
            can_vote = self.voted_for in (None, req.candidate_id)

            if can_vote and up_to_date:
                self.voted_for = req.candidate_id
                self._last_heartbeat = time.monotonic()
                return VoteResponse(term=self.current_term, vote_granted=True, voter_id=self.id)

            return VoteResponse(term=self.current_term, vote_granted=False, voter_id=self.id)

    async def handle_append_entries(self, req: AppendEntriesRequest):
        async with self._lock:
            if req.term < self.current_term:
                return AppendEntriesResponse(term=self.current_term, success=False, match_index=len(self.log), follower_id=self.id, follower_len=len(self.log))

            self._last_heartbeat = time.monotonic()

            if req.term > self.current_term:
                self.current_term = req.term
                self.voted_for = None

            # 🔥 CRITICAL: step down even if same term
            self.state = "follower"
            self.leader_id = req.leader_id

            # prevLogIndex consistency check — restarted/lagging follower rejects here
            if req.prev_log_index > 0:
                if len(self.log) < req.prev_log_index:
                    # follower log is too short — respond with current length so leader knows where to sync from
                    return AppendEntriesResponse(term=self.current_term, success=False, match_index=len(self.log), follower_id=self.id, follower_len=len(self.log))
                if self.log[req.prev_log_index - 1].term != req.prev_log_term:
                    # term mismatch at prevLogIndex — truncate and reject
                    self.log = self.log[: req.prev_log_index - 1]
                    return AppendEntriesResponse(term=self.current_term, success=False, match_index=len(self.log), follower_id=self.id, follower_len=len(self.log))

            # append entries
            for e in req.entries:
                entry = LogEntry(index=e.index, term=e.term, payload=e.payload)
                if entry.index <= len(self.log):
                    if self.log[entry.index - 1].term != entry.term:
                        self.log = self.log[: entry.index - 1]
                        self.log.append(entry)
                else:
                    self.log.append(entry)

            if req.leader_commit > self.commit_index:
                self.commit_index = min(req.leader_commit, len(self.log))

            return AppendEntriesResponse(term=self.current_term, success=True, match_index=len(self.log), follower_id=self.id, follower_len=len(self.log))

    async def handle_sync_log(self, req):
        """Called by the leader on a lagging follower — follower receives all missing committed entries."""
        async with self._lock:
            for e in req.entries:
                entry = LogEntry(index=e.index, term=e.term, payload=e.payload)
                if entry.index <= len(self.log):
                    if self.log[entry.index - 1].term != entry.term:
                        self.log = self.log[: entry.index - 1]
                        self.log.append(entry)
                else:
                    self.log.append(entry)

            if self.commit_index > 0:
                self.commit_index = min(self.commit_index, len(self.log))

            logger.info("%s synced log from index %s, now has %s entries", self.id, req.from_index, len(self.log))

        from .message_types import SyncLogResponse
        return SyncLogResponse(
            term=self.current_term,
            entries=[
                LogEntryModel(index=e.index, term=e.term, payload=e.payload)
                for e in self.log[:self.commit_index]
            ],
            commit_index=self.commit_index,
        )

    async def _push_sync_to_follower(self, peer: str, from_index: int):
        """Leader pushes all committed entries from from_index onwards to a lagging follower."""
        try:
            entries = [
                LogEntryModel(index=e.index, term=e.term, payload=e.payload)
                for e in self.log[from_index : self.commit_index]
            ]
            resp = await self._client.post(
                f"{peer}/sync-log",
                json={"from_index": from_index, "entries": [e.model_dump() for e in entries]},
            )
            if resp.status_code == 200:
                logger.info("Sync pushed to %s from index %s", peer, from_index)
                self.next_index[peer] = self.commit_index + 1
                self.match_index[peer] = self.commit_index
        except Exception as exc:
            logger.warning("Failed to push sync to %s: %s", peer, exc)

    async def handle_submit_stroke(self, req: SubmitStrokeRequest):
        if self.state != "leader":
            return {"accepted": False, "leader": self.leader_id}

        async with self._lock:
            new_index = len(self.log) + 1
            entry = LogEntry(index=new_index, term=self.current_term, payload=req.stroke)
            self.log.append(entry)

        await self.replicate([entry])

        return {"accepted": True, "index": new_index}

    async def snapshot(self):
        return HealthResponse(
            id=self.id,
            term=self.current_term,
            state=self.state,
            leader_id=self.leader_id,
            log_length=len(self.log),
            commit_index=self.commit_index,
        )

    async def committed_log(self):
        return [e.payload for e in self.log[: self.commit_index]]