export interface Env {
  DB: D1Database;
  TELEGRAM_BOT_TOKEN: string;
  TELEGRAM_WEBHOOK_SECRET: string;
  TELEGRAM_OWNER_ID: string;
  TELEGRAM_ALLOWED_CHAT_IDS?: string;
}

type ObjectValue = Record<string, unknown>;
const object = (value: unknown): value is ObjectValue =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const integer = (value: unknown): value is number =>
  typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const identifier = (value: unknown): value is string =>
  typeof value === 'string' && value.length > 0 && value.length <= 256;
const decimal = (value: string): boolean =>
  /^[1-9][0-9]*$/.test(value) && Number.isSafeInteger(Number(value));
const response = (status = 200): Response =>
  new Response(status === 200 ? 'OK' : 'Rejected', {
    status, headers: { 'Cache-Control': 'no-store', 'Content-Type': 'text/plain' },
  });

async function authenticated(actual: string | null, expected: string): Promise<boolean> {
  if (!actual || actual.length > 256) return false;
  // Compare fixed-length digests; never log headers or credentials.
  const digest = async (s: string) => new Uint8Array(
    await crypto.subtle.digest('SHA-256', new TextEncoder().encode(s)),
  );
  const [a, b] = await Promise.all([digest(actual), digest(expected)]);
  let difference = 0;
  for (let i = 0; i < a.length; i++) difference |= a[i] ^ b[i];
  return difference === 0;
}

async function boundedJson(request: Request): Promise<unknown> {
  if (!request.body) throw new Error('Invalid body');
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > 256 * 1024) throw new Error('Invalid body');
      chunks.push(value);
    }
  } finally {
    await reader.cancel();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.length; }
  return JSON.parse(new TextDecoder('utf-8', { fatal: true, ignoreBOM: false }).decode(bytes));
}

async function handle(request: Request, env: Env): Promise<Response> {
  const url = new URL(request.url);
  if (url.protocol !== 'https:' || url.pathname !== '/telegram/webhook' || request.method !== 'POST') {
    return response(404);
  }
  if (!/^[1-9][0-9]*:[A-Za-z0-9_-]+$/.test(env.TELEGRAM_BOT_TOKEN ?? '') ||
      !/^[A-Za-z0-9_-]{1,256}$/.test(env.TELEGRAM_WEBHOOK_SECRET ?? '') ||
      !decimal(env.TELEGRAM_OWNER_ID ?? '')) return response(503);
  if (!await authenticated(request.headers.get('X-Telegram-Bot-Api-Secret-Token'), env.TELEGRAM_WEBHOOK_SECRET)) {
    return response(401);
  }
  const allowed = (env.TELEGRAM_ALLOWED_CHAT_IDS ?? '').split(',').map(s => s.trim()).filter(Boolean);
  if (allowed.some(id => !decimal(id))) return response(503);

  let update: unknown;
  try { update = await boundedJson(request); } catch { return response(400); }
  if (!object(update) || !integer(update.update_id)) return response(400);
  const connectionEvent = object(update.business_connection) ? update.business_connection : null;
  const message = object(update.business_message) ? update.business_message : null;
  if (connectionEvent && message) return response(400);
  if (!connectionEvent && !message) return response();
  const event = (connectionEvent ?? message)!;
  const connectionId = connectionEvent ? event.id : event.business_connection_id;
  if (!identifier(connectionId) || !integer(event.date)) return response(400);
  let chatId: string | null = null;
  let messageId: number | null = null;
  if (message) {
    if (!object(message.chat) || message.chat.type !== 'private') return response();
    if (!integer(message.chat.id) || message.chat.id === 0 ||
        !integer(message.message_id) || message.message_id === 0) return response(400);
    chatId = String(message.chat.id);
    messageId = message.message_id;
    if (!allowed.includes(chatId)) return response();
  }
  // Never trust the owner supplied in the webhook, nor cache an authorization
  // across updates: the owner can disable the business connection at any time.
  const telegram = await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/getBusinessConnection`, {
    method: 'POST', redirect: 'manual',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ business_connection_id: connectionId }),
    signal: AbortSignal.timeout(8000),
  });
  if (!telegram.ok) return response(503);
  const result: unknown = await telegram.json();
  if (!object(result) || result.ok !== true || !object(result.result)) return response(503);
  const connection = result.result;
  if (connection.id !== connectionId || !object(connection.user) ||
      !integer(connection.user.id) || typeof connection.is_enabled !== 'boolean') return response(503);
  if (String(connection.user.id) !== env.TELEGRAM_OWNER_ID) return response();
  if (message && !connection.is_enabled) return response();

  // Exactly this projection is persisted. No JSON serialization of the payload.
  // Connection events have no message/chat identity. Record current verified
  // enabled state, not the potentially stale value in a retried webhook.
  const saved = await env.DB.prepare(`
    INSERT INTO events (bot_id, update_id, event_type, connection_id,
      event_time, received_at, chat_id, message_id, connection_enabled)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING
  `).bind(
    env.TELEGRAM_BOT_TOKEN.split(':')[0], update.update_id,
    connectionEvent ? 'business_connection' : 'business_message', connectionId,
    event.date, Math.floor(Date.now() / 1000), chatId, messageId,
    connectionEvent ? Number(connection.is_enabled) : null,
  ).run();
  return response(saved.success ? 200 : 503);
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    // Fetch/D1 exceptions can contain URLs, credentials or payloads. Do not
    // propagate them into Workers error telemetry or Telegram responses.
    try { return await handle(request, env); } catch { return response(503); }
  },
};
