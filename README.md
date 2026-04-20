# Distributed Raft Project
#

This project is a minimal end-to-end scaffold demonstrating a distributed whiteboard using a simplified RAFT consensus mechanism. It includes a WebSocket gateway, three RAFT replicas, and a static frontend canvas. Everything is containerized using Docker Compose with hot reload enabled.

## Project Structure

```
gateway/        # FastAPI WebSocket gateway
replica1/       # RAFT node (configured via env)
replica2/       # RAFT node (configured via env)
replica3/       # RAFT node (configured via env)
shared/         # Shared RAFT logic and schemas
frontend/       # Static canvas client
gateway/Dockerfile
replica/Dockerfile
docker-compose.yml
```

---

## 🚀 Quick Start

```bash
# From project root
docker-compose up --build
```

Then open:

```
http://localhost:8080
```

Open multiple tabs and draw to see real-time synchronization.

---

##  How It Works (Simplified)

###  RAFT Replicas

* Each replica runs a `RaftNode` with states:

  * Follower
  * Candidate
  * Leader
* Election timeout: **500–800 ms (randomized)**
* Heartbeats: **every 150 ms**\

 Gateway

* Maintains a list of replica URLs
* Periodically checks `/health` to detect the current leader
* WebSocket:

  * Receives strokes from clients
  * Forwards them to leader via `/submit-stroke`
  * Broadcasts committed strokes to all connected clients

### Replication Flow

1. Client sends stroke → Gateway
2. Gateway forwards to leader (`/submit-stroke`)
3. Leader:

   * Appends to its log
   * Sends `AppendEntries` to followers
4. Once quorum is reached:

   * Entry is committed
   * Broadcast to clients
5. Followers:

   * Sync missing entries via `/sync-log`

---

##  Key Features

* Leader election using RAFT principles
* Log replication with quorum-based commit
* WebSocket-based real-time updates
* Automatic leader discovery via gateway
* In-memory logs for simplicity
* Hot reload via Docker bind mounts

---

## 🔗 Replica API Endpoints

| Endpoint          | Method | Description                  |
| ----------------- | ------ | ---------------------------- |
| `/request-vote`   | POST   | Election voting              |
| `/append-entries` | POST   | Log replication + heartbeat  |
| `/sync-log`       | POST   | Catch-up missing log entries |
| `/submit-stroke`  | POST   | Client write (leader only)   |
| `/log`            | GET    | Get committed strokes        |
| `/health`         | GET    | Node state snapshot          |

---

##  Notes

* Logs are stored **in-memory** (not persistent)
* Designed for **educational/demo purposes**
* Works best with **3 replicas** (odd number for quorum)

---

##  Testing Ideas

* Kill a follower → system should continue normally
* Kill leader → new leader should be elected
* Restart a node → should catch up via `/sync-log`
* Open multiple browser tabs → verify live sync

---

##  Tech Stack

* FastAPI (Python)
* WebSockets
* Docker & Docker Compose
* Basic RAFT consensus implementation

##
