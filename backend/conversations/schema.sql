-- Conversation list metadata. Message history itself lives in the LangGraph
-- PostgresSaver tables (checkpoints, checkpoint_blobs, checkpoint_writes) keyed by thread_id.
CREATE TABLE IF NOT EXISTS chat_threads (
    thread_id        TEXT PRIMARY KEY,
    child_id         TEXT NOT NULL,
    title            TEXT NOT NULL DEFAULT '',
    created_at       TIMESTAMPTZ NOT NULL,
    last_message_at  TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS chat_threads_child_last ON chat_threads (child_id, last_message_at);
