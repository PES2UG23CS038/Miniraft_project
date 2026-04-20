from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel


class LogEntryModel(BaseModel):
    index: int
    term: int
    payload: Dict[str, Any]


class VoteRequest(BaseModel):
    term: int
    candidate_id: str
    last_log_index: int
    last_log_term: int


class VoteResponse(BaseModel):
    term: int
    vote_granted: bool
    voter_id: str


class AppendEntriesRequest(BaseModel):
    term: int
    leader_id: str
    prev_log_index: int
    prev_log_term: int
    entries: List[LogEntryModel]
    leader_commit: int


class AppendEntriesResponse(BaseModel):
    term: int
    success: bool
    match_index: int
    follower_id: str
    follower_len: int


class SyncLogRequest(BaseModel):
    from_index: int
    entries: List[LogEntryModel]


class SyncLogResponse(BaseModel):
    term: int
    entries: List[LogEntryModel]
    commit_index: int


class SubmitStrokeRequest(BaseModel):
    stroke: Dict[str, Any]


class HealthResponse(BaseModel):
    id: str
    term: int
    state: str
    leader_id: Optional[str]
    log_length: int
    commit_index: int
