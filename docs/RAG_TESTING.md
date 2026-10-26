# RAG Voice Agent Testing & Verification Guide

This guide provides end-to-end procedures for verifying the Retrieval-Augmented Generation (RAG) HTTP pipeline during live and simulated voice calls.

---

## 1. Prerequisites & Test Infrastructure

### 1.1 Running the Mock RAG Server
A mock RAG endpoint is provided in `tests/mock_rag_server.py`. To run it locally:
```bash
cd hr_voice_agent
../.venv/bin/uvicorn tests.mock_rag_server:app --port 9100 --host 127.0.0.1
```

Available endpoints on the mock server:
- `POST /ok` or `GET /ok`: Returns 3 policy items from `leave-policy.pdf` (score ~0.82).
- `POST /alt`: Returns alternate nested schema `{"data": {"matches": [{"text": "..."}]}}`.
- `POST /single`: Returns scalar answer `{"answer": "Standard probation period is three months."}`.
- `POST /empty`: Returns `{"results": []}`.
- `POST /slow?delay=3`: Sleeps for 3.0s before responding with `/ok` payload.
- `POST /error`: Returns HTTP 500 error.
- `POST /badjson`: Returns HTML text failing JSON parsing.
- `POST /auth`: Requires `Authorization: Bearer test-token` (returns 401 otherwise).

### 1.2 Core Environment Variables
Set these variables before starting the voice worker (`agent.py`) and token server:
```bash
# Allow HTTP scheme in development (SSRF bypass)
export RAG_ALLOW_INSECURE_DEV="true"

# Whitelist local mock server address
export RAG_ALLOWED_HOSTS="127.0.0.1,localhost"

# Secret encryption key for storing customer bearer tokens / API headers
export RAG_SECRET_KEY="D3nGn7oIbKEk1qT7A7JXR9mW2Dsy0hCwdFiVi8mXxaQ="

# Permit loading knowledgeBase directly from job metadata in dev mode
export RAG_ALLOW_METADATA_CONFIG="true"

# Filler delay and timeout tuning (defaults: delay=0.8s, timeout=2.0s)
export RAG_FILLER_DELAY="0.8"
export RAG_TIMEOUT="2.0"
export RAG_FILLER_MODE="text"
```

---

## 2. Manual Call Test Checklist

### Scenario 1: Policy Question Answered Using Excerpts
*Goal: Confirm general policy queries trigger RAG retrieval and ground the LLM reply in retrieved snippets.*

- **Mock Server Setup**: Run mock server on port 9100.
- **Agent KnowledgeBase Configuration**:
  ```json
  {
    "ragEnabled": true,
    "groundingMode": "balanced",
    "topK": 3,
    "externalRag": {
      "url": "http://127.0.0.1:9100/ok",
      "method": "POST",
      "queryKey": "query",
      "responsePath": "results[].content"
    }
  }
  ```
- **Utterance**: "What is the policy on casual leaves?"
- **Expected Voice Behavior**:
  - Voice agent answers stating that employees get 12 casual leaves per year (from `leave-policy.pdf`).
- **Log Verification**:
  ```
  INFO  rag-retriever-service: RAG search completed: 3 snippets retrieved in 0.045s
  INFO  agent: RAG turn: gate=True, latency_ms=48.20, snippet_count=3, filler_played=False, error_type=None
  ```

---

### Scenario 2: Personal Question Does NOT Trigger RAG
*Goal: Ensure employee self-service queries (leave balance, payslip) bypass RAG and route directly to DB tools.*

- **Mock Server Setup**: Mock server running on port 9100.
- **Agent KnowledgeBase Configuration**: Same as Scenario 1.
- **Utterance**: "How many casual leaves do I have left?" or "What is my current leave balance?"
- **Expected Voice Behavior**:
  - RAG gate skips retrieval immediately.
  - LLM invokes `check_leave_balance` or queries employee record directly.
  - Zero network calls to the mock RAG server.
- **Log Verification**:
  - No `RAG turn: gate=True` logged.
  - Mock server access log shows 0 requests.

---

### Scenario 3: Follow-Up Policy Question
*Goal: Verify that multi-turn context carries over correctly without snippet pollution across turns.*

- **Turn 1 Utterance**: "Tell me about the sick leave rules."
  - Agent replies with sick leave excerpts (doctor certificate needed for >2 days).
- **Turn 2 Utterance**: "And how many days was that without a doctor's note?"
- **Expected Voice Behavior**:
  - Turn 2 correctly recognizes the context from prior turn and responds: "Up to two consecutive days without a doctor's certificate."
  - Grounding instructions injected into Turn 1 do not persist in LLM context for Turn 2.
- **Log Verification**:
  - `gate=True` evaluated on both turns.

---

### Scenario 4: Caller Interrupts the Filler
*Goal: Verify that when retrieval latency triggers a filler ("One moment while I check that."), the caller can barge in and interrupt it cleanly.*

- **Mock Server Setup**: Point to `/slow?delay=3` or introduce latency.
- **Utterance**: "What is the maternity leave entitlement?"
- **Expected Voice Behavior**:
  - At 800ms, agent begins playing filler: "Let me check that for you."
  - Caller immediately speaks: "Wait, actually never mind, tell me about holidays."
  - Agent immediately stops speaking the filler, cancels previous generation, and pivots to answering about holidays.
- **Log Verification**:
  - Speech handle cancellation logged.
  - `filler_played=True` logged for turn 1.

---

### Scenario 5: External Endpoint Down (Resilience & Circuit Breaker)
*Goal: Verify that an unreachable or failing retrieval endpoint NEVER breaks, crashes, or hangs a live voice call.*

- **Mock Server Setup**: Stop the mock server (`Ctrl+C`), or configure URL to an unused port like `http://127.0.0.1:9199/ok`.
- **Utterance 1**: "What is the policy for notice periods?"
- **Expected Voice Behavior**:
  - Call does NOT hang or disconnect.
  - Agent seamlessly replies using fallback knowledge or states general information.
- **Utterance 2 & 3**: Two more policy questions.
- **Utterance 4**:
  - On the 4th failure, the circuit breaker opens (`is_open=True`).
  - Retrieval returns `[]` immediately (0ms) without attempting network calls.
- **Log Verification**:
  ```
  WARNING agent: RAG retrieval failed: ClientConnectorError
  WARNING rag-retriever-service: RAG circuit breaker is open; skipping retrieval without calling endpoint
  ```

---

### Scenario 6: Slow Endpoint (Filler Playback)
*Goal: Ensure smooth caller experience during slow network retrievals.*

- **Mock Server Setup**: Configure URL to `http://127.0.0.1:9100/slow?delay=1.5`.
- **Utterance**: "Can you explain our probation policy?"
- **Timeline**:
  - 0ms: User stops speaking. Search task dispatched.
  - 800ms: RAG_FILLER_DELAY expires -> filler audio plays: "One moment while I check that."
  - 1500ms: Search finishes with snippets.
  - ~1600ms: LLM generation streams policy answer immediately following the filler.
- **Log Verification**:
  ```
  INFO agent: RAG turn: gate=True, latency_ms=1520.10, snippet_count=3, filler_played=True, error_type=None
  ```

---

### Scenario 7: Strict Mode with Empty Results
*Goal: Test strict adherence when no matching snippets are found.*

- **Mock Server Setup**: Run mock server, pointing agent URL to `http://127.0.0.1:9100/empty`.
- **Agent KnowledgeBase Configuration**:
  ```json
  {
    "ragEnabled": true,
    "groundingMode": "strict",
    "externalRag": {
      "url": "http://127.0.0.1:9100/empty"
    }
  }
  ```
- **Utterance**: "What is the policy on sabbatical leave?"
- **Expected Voice Behavior**:
  - Agent explicitly declines to answer with unverified data, stating it does not have that information and offering to transfer to an HR coordinator or raise an HR ticket.
- **Log Verification**:
  ```
  INFO agent: RAG turn: gate=True, latency_ms=42.10, snippet_count=0, filler_played=False, error_type=None
  ```

---

### Scenario 8: RAG Disabled
*Goal: Confirm that disabling RAG turns off all gate checks and network queries.*

- **Agent KnowledgeBase Configuration**:
  ```json
  {
    "ragEnabled": false
  }
  ```
- **Utterance**: "What is the policy on casual leaves?"
- **Expected Voice Behavior**:
  - `self._retriever` is `None`.
  - Gate is bypassed without executing network operations.
  - Agent answers from base prompt or tools.
