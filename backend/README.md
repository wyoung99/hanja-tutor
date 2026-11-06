# hanja-tutor backend

```
python -m venv .venv                                           # at the repo root (gitignored)
.venv/Scripts/python -m pip install -r backend/requirements.txt   # macOS/Linux: .venv/bin/python
cd backend && ../.venv/Scripts/python -m uvicorn app.main:app --port 8000   # frontend: BACKEND_URL=http://127.0.0.1:8000
```

**Why the repo-root `.venv` is required:** the npm test scripts call plain `python`. The Ouroboros evaluator runs
Stage 1 (`npm --prefix frontend run test:all`) inside its MCP server's environment, where `python` resolves to the
server's own isolated interpreter (no psycopg, pixeltable-pgserver, langgraph). It puts the checkout's `.venv`
(or `venv`) first on `PATH`, so with a repo-root `.venv` holding `backend/requirements.txt` Stage 1 uses the right
interpreter. Without it Stage 1 fails with `ModuleNotFoundError: No module named 'psycopg'` (seen in evaluation
job_e52c689e277e) or, before psycopg was a hard import, silently skipped the embedded-PostgreSQL tests.

## Configuration (`backend/.env`)

Copy `backend/.env.example` to `backend/.env` (gitignored, never commit it) and fill in what you need.
The app reads it at startup (`app/settings.py`); variables already set in the process environment win,
and empty values count as unset.

| key | effect |
|-----|--------|
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (+ `AWS_SESSION_TOKEN` only for temporary keys) | Bedrock credentials, read by the default boto3 chain |
| `AWS_REGION` | Bedrock region (default `ap-northeast-2`, `global.*` profiles) |
| `LLM_CONFIG` | `{"provider":"bedrock","region":"ap-northeast-2"}` -> Haiku 4.5 (chat, safety) / Sonnet 4.6 (question generation). Empty -> fake LLM |
| `LLM_HEDGE_AFTER_S` | hedged requests for the chatbot answer stream and the safety classifier (Haiku only, never Sonnet question generation): if the first chunk has not arrived after this many seconds, ONE duplicate request is sent and the faster one wins. Default `1.5`, `0` disables. Counts (calls / hedged / hedge_won) in `GET /api/health` -> `llm.hedge` |
| `OPENDICT_API_KEY` | 우리말샘 open API key (fallback for hanja missing from the DB). Empty -> "잘 모르겠어요" |
| `DATABASE_URL` / `PG_EMBEDDED_DIR` | PostgreSQL as the system of record (see below). `PG_EMBEDDED_DIR=.pgdata` starts the bundled pip PostgreSQL (pixeltable-pgserver, repo-local data dir); neither -> everything in memory |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | Web Push to parents' browsers (base64url P-256 public point / raw private key; subject `mailto:` or `https://`). Generate with `cd backend && python -m notifications.webpush keygen`. Empty -> push disabled, in-app inbox + email fallback only |
| `STROKE_TOL_POSITION`, `STROKE_TOL_LENGTH_MIN`, `STROKE_TOL_LENGTH_MAX`, `STROKE_TOL_DIRECTION_DEG` | stroke grading tolerances (defaults: 350, 0.5, 2.0, 60) |
| `USAGE_CHATBOT_DAILY_LIMIT`, `USAGE_MOCKEXAM_WEEKLY_LIMIT` | free-plan limits (20 / 1) |
| `ADMIN_TOKEN` | operator endpoints (`X-Admin-Token`): refunds review, `/api/admin/jobs/*` |

## Persistence (PostgreSQL)

With `DATABASE_URL` or `PG_EMBEDDED_DIR` every piece of state survives a restart:

* conversations: LangGraph `PostgresSaver` checkpoints + `chat_threads` (unchanged);
* usage counters: `usage_counters (child_id, feature, period_start, used)`; each request is ONE conditional
  `INSERT .. ON CONFLICT DO UPDATE SET used = used + 1 WHERE used < limit RETURNING`, so two devices (or two server
  processes) can never pass the limit (AC13);
* everything else: one table per entity (`parents`, `children`, `dormant_parents`, `dormant_children`,
  `parent_sessions` (token hashes), `study_plans`, `review_items`, `tracing_progress`, `tracing_attempts`, `mock_exams`,
  `report_days`, `review_answers`, `weekly_summaries_sent`, `screentime_settings`, `screentime_days`, `parent_pins`,
  `extension_requests`, `safety_events`, `safety_thread_counts`, `chat_locks`, `paid_usage_events`,
  `usage_limit_notices`, `subscriptions`, `payments`, `payment_archive`, `refund_requests`, `dormant_vault`,
  `notifications`, `push_subscriptions`), each `(key, child_id, parent_id, doc jsonb, updated_at)`. Schema creation is
  idempotent (`CREATE .. IF NOT EXISTS`, version row in `app_schema_version`); no ORM (`db/state.py`,
  `app/persistence.py`).

The services keep their working set in memory and `StateSync` writes the diff of every request in one transaction
**before the response is sent** (ASGI middleware; again after streamed responses and background jobs). At startup the
tables are loaded back. Run one server process per database. Deleting a child purges memory, flushes, then deletes every
row carrying the child id; `remaining_learning_records` counts all tables, `usage_counters` and the child's former
checkpoints (AC3). Deleting the account moves its `payments` rows to `payment_archive` in the same transaction.
Unit tests use the in-memory implementations behind the same interfaces.

`python backend/scripts/restart_survival.py` demonstrates it on real processes (uvicorn #1 writes, uvicorn and
PostgreSQL stop, uvicorn #2 reads) -> `reports/restart-survival.json`.

## Notifications (Web Push)

Every parent notification (AC8 safety alert, AC11 extension request, AC12 weekly summary for subscribers, AC13 free-limit
upgrade prompt, billing, dormancy) goes to the in-app inbox (`GET /api/parents/me/notifications`) and, with VAPID keys,
as an encrypted Web Push (pywebpush, aes128gcm) to every browser the parent registered in 알림 > 기기 푸시 알림
(`GET /api/push/config` -> `POST/DELETE /api/parents/me/push-subscriptions`). A 404/410 answer deletes the subscription;
if nothing was delivered the notification is emailed instead (Seed assumption; billing failures are emailed by billing
itself). Push payloads are `{title, body, kind, id, tag, url}` built from system text only - never a child's chat text.
Only parent sessions can subscribe, so upgrade prompts never reach a child's device.

## Tests

| command | what |
|---------|------|
| `npm --prefix frontend run test:all` | Stage 1 command: `test:unit` then `test:smoke` (both must pass) |
| `npm --prefix frontend run test:unit` | vitest + backend pytest (hermetic: never reads `.env`). PostgreSQL tests share ONE embedded server per session (fresh database per test, stopped and deleted at the end); if it cannot start they ERROR with diagnostics (also `reports/pg-startup-error.log`) instead of skipping (`HANJA_PG_OPTIONAL=1` to skip, `TEST_DATABASE_URL` for an external server) |
| `npm --prefix frontend run test:smoke` | `pytest -m live_smoke`: real calls -- 1 Haiku chat answer (DB 훈·음, 3~5 sentences), 1 Haiku classifier, 1 Sonnet question generation through the DB gate, 1 우리말샘 lookup, 30-question first-token p95 on a local uvicorn -> `reports/p95-eval.json` (fails if p95 >= 3 s). About 25 Bedrock calls (mostly Haiku) + 1 dictionary call per run. Reads `backend/.env` (or the file in `HANJA_DOTENV`). **Without AWS credentials (or `OPENDICT_API_KEY`) these tests FAIL, they do not skip** - Stage 1 is meant to prove real calls |
| `npm --prefix frontend run test:live` | opt-in real Bedrock + 우리말샘 tests (reads `.env`; skips with "AWS credentials not configured" without keys) |
| `npm --prefix frontend run e2e` | Playwright on local uvicorn + next start (fake LLM; `E2E_REAL_LLM=1` to use `.env`; fresh embedded PostgreSQL in `.pgdata/e2e`; throw-away VAPID keys) -> `reports/e2e-results.json` |
| `npm --prefix frontend run measure:p95` | AC9 first-token p95 on a local uvicorn with real Bedrock -> `reports/p95.json` |
| `npm --prefix frontend run lighthouse` | Lighthouse PWA audit on local next start -> `reports/lighthouse-pwa.{json,html}` |
| `python backend/scripts/exam_sample_review.py` | >=200 mock-exam questions -> `reports/exam_sample_review.csv` for human review (`--tally` afterwards) |
