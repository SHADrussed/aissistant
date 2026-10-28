-- No raw payload, text, caption, sender name, media or credentials.
CREATE TABLE events (
  bot_id TEXT NOT NULL,
  update_id INTEGER NOT NULL CHECK (update_id >= 0),
  event_type TEXT NOT NULL CHECK (event_type IN ('business_connection', 'business_message')),
  connection_id TEXT NOT NULL,
  event_time INTEGER NOT NULL CHECK (event_time >= 0),
  received_at INTEGER NOT NULL,
  chat_id TEXT,
  message_id INTEGER,
  connection_enabled INTEGER CHECK (connection_enabled IN (0, 1)),
  PRIMARY KEY (bot_id, update_id),
  CHECK (
    (event_type = 'business_message' AND chat_id IS NOT NULL AND message_id > 0 AND connection_enabled IS NULL)
    OR (event_type = 'business_connection' AND chat_id IS NULL AND message_id IS NULL AND connection_enabled IS NOT NULL)
  )
) STRICT;
CREATE UNIQUE INDEX message_identity
  ON events(bot_id, connection_id, chat_id, message_id)
  WHERE event_type = 'business_message';
CREATE INDEX events_time ON events(event_time);
