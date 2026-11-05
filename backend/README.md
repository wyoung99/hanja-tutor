# hanja-tutor backend

```
python -m pip install -r backend/requirements.txt
cd backend && python -m uvicorn app.main:app --port 8000      # frontend: BACKEND_URL=http://127.0.0.1:8000
```

## Configuration (`backend/.env`)

Copy `backend/.env.example` to `backend/.env` (gitignored, never commit it) and fill in what you need.
The app reads it at startup (`app/settings.py`); variables already set in the process environment win,
and empty values count as unset.

| key | effect |
|-----|--------|
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` (+ `AWS_SESSION_TOKEN` only for temporary keys) | Bedrock credentials, read by the default boto3 chain |
| `AWS_REGION` | Bedrock region (default `ap-northeast-2`, `global.*` profiles) |
| `LLM_CONFIG` | `{"provider":"bedrock","region":"ap-northeast-2"}` -> Haiku 4.5 (chat, safety) / Sonnet 4.6 (question generation). Empty -> fake LLM |
| `OPENDICT_API_KEY` | 우리말샘 open API key (fallback for hanja missing from the DB). Empty -> "잘 모르겠어요" |
| `DATABASE_URL` / `PG_EMBEDDED_DIR` | conversations in PostgreSQL (LangGraph PostgresSaver + `chat_threads`). `PG_EMBEDDED_DIR=.pgdata` starts the bundled pip PostgreSQL (pixeltable-pgserver); neither -> in memory |
| `STROKE_TOL_POSITION`, `STROKE_TOL_LENGTH_MIN`, `STROKE_TOL_LENGTH_MAX`, `STROKE_TOL_DIRECTION_DEG` | stroke grading tolerances (defaults: 350, 0.5, 2.0, 60) |
| `USAGE_CHATBOT_DAILY_LIMIT`, `USAGE_MOCKEXAM_WEEKLY_LIMIT` | free-plan limits (20 / 1) |
| `ADMIN_TOKEN` | operator endpoints (`X-Admin-Token`): refunds review, `/api/admin/jobs/*` |

## Tests

| command | what |
|---------|------|
| `npm --prefix frontend run test:all` | vitest + backend pytest (hermetic: never reads `.env`; PostgreSQL tests use an embedded server in a temp dir) |
| `npm --prefix frontend run test:live` | opt-in real Bedrock + 우리말샘 tests (reads `.env`; skips with "AWS credentials not configured" without keys) |
| `npm --prefix frontend run e2e` | Playwright on local uvicorn + next start (fake LLM; `E2E_REAL_LLM=1` to use `.env`) -> `reports/e2e-results.json` |
| `npm --prefix frontend run measure:p95` | AC9 first-token p95 on a local uvicorn with real Bedrock -> `reports/p95.json` |
| `npm --prefix frontend run lighthouse` | Lighthouse PWA audit on local next start -> `reports/lighthouse-pwa.{json,html}` |
| `python backend/scripts/exam_sample_review.py` | >=200 mock-exam questions -> `reports/exam_sample_review.csv` for human review (`--tally` afterwards) |
