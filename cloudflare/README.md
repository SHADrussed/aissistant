# Telegram Business → Cloudflare: метаданные, фаза 1

Отдельный Worker на TypeScript и база D1. Не зависит от Python-приложения,
Railway, Telethon, Gmail или OpenAI. Настройки v0.2 не меняются.
**Этот PR не выполняет deploy и не регистрирует webhook.**

## Поведение и границы доступа

`POST https://<worker>/telegram/webhook` проверяет секретный заголовок до разбора
JSON. Принимает `business_connection` и `business_message`; остальные события
подтверждает без обработки. Только личные чаты с ID из явного allowlist.
Пустой список запрещает все сообщения. Обычный `message` не принимается.

Для каждого подходящего события Worker вызывает **только** `getBusinessConnection`
и сверяет `result.id`, `result.user.id` с `TELEGRAM_OWNER_ID`. Полю `user` из
webhook не доверяет. Сообщения отключённого подключения игнорируются.
Включённость не кэшируется; повторная доставка также проходит проверку владельца.
События подключения сохраняются даже при выключенном подключении, если владелец
подтверждён. Они не содержат переписки и не требуют allowlist чата.

D1 сохраняет: ID бота (не токен), update ID, connection ID, тип события,
время события Telegram (Unix seconds), время приёма, chat ID и message ID.
У события подключения последние два поля `NULL`; дополнительный
`connection_enabled` отражает **текущее проверенное** состояние, а не историческое
состояние при отправке webhook. Это журнал доставки, не полная история настроек.
Числовые ID проверяются на безопасное целое; chat/owner/bot ID представляются
строками. Подготовленные запросы D1 исключают интерполяцию входных значений в SQL.

Первичный ключ `(bot_id, update_id)` подавляет повторы. Дополнительный уникальный
индекс `(bot_id, connection_id, chat_id, message_id)` подавляет повторное сообщение
с другим update ID. Успех возвращается только после завершения записи D1.
Конкурентные дубликаты разрешает сама база, состояние памяти не используется.
Разные подключения не смешиваются. Миграции лежат в `migrations/`; Wrangler
ведёт собственную таблицу применённых миграций.

Текст, подписи, имена, вложения, сырые JSON и заголовки **не сохраняются**.
Однако Telegram присылает их в теле webhook: они кратковременно находятся в памяти
Worker и проходят через инфраструктуру Cloudflare. «Только метаданные» не означает,
что текст вообще не передаётся Cloudflare. Метаданные тоже являются личными данными.
Нет отправки/редактирования/удаления сообщений, отметок прочтения, OpenAI или
доступных по HTTP методов чтения базы. GET, `/archive`, `/search`, `/mcp` → 404.

Неподлинный запрос → 401. Некорректный JSON/тело больше 256 KiB → 400.
Отфильтрованное событие → 200 без записи. Ошибки Telegram, конфигурации или D1 →
503 для повторной доставки. Постоянные ошибки авторизации/API и слишком большие
обновления могут приводить к повторным доставкам: контролируйте ошибки webhook.
Нет обещания безусловной доставки: Telegram хранит ожидающие обновления ограниченное
время; длительный сбой требует внимания владельца. История через API не загружается.

Логи приложения отсутствуют, ошибки наружу обезличены. Observability и preview URL
выключены. Не включайте HTTP body/header logging, traces или `wrangler tail` с
полезной нагрузкой. Bot API содержит токен в URL исходящего запроса: внешние средства
трассировки тоже должны быть выключены/редактировать URL. Cloudflare-администратор
и обладатель D1 API-доступа могут читать метаданные — ограничьте доступ к аккаунту.
Обладатель webhook secret может подделывать события; API подтверждает владельца
подключения, но не подлинность отдельного сообщения. При утечке смените секрет.

## Переменные

| Имя | Где | Значение |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Cloudflare **Secret** | Токен нового отдельного Business-бота |
| `TELEGRAM_WEBHOOK_SECRET` | Cloudflare **Secret** | Случайный секрет, 1–256 символов `A-Z a-z 0-9 _ -`; рекомендуются 32 случайных байта |
| `TELEGRAM_OWNER_ID` | Cloudflare **Secret** | Положительный числовой Telegram ID владельца Business-аккаунта, не username и не ID бота |
| `TELEGRAM_ALLOWED_CHAT_IDS` | Worker variable в личной конфигурации | Положительные числовые ID разрешённых личных чатов через запятую; по умолчанию пусто |
| `DB` | D1 binding | Отдельная база `aissistant-business-metadata` |

Секреты не нужны ни для сборки, ни для тестов. `.dev.vars*`, `.env*`, локальная
конфигурация, D1-файлы и `node_modules` исключены из Git. Не присылайте секреты в
Codex. Не используйте токен существующего бота уведомлений Railway: webhook у бота
один, его замена может нарушить другой обработчик.

## Локальная проверка без аккаунтов

Linux/macOS/Windows с поддерживаемыми Node.js 22.12+ и Python 3.12:

```sh
cd cloudflare
npm ci
npm run check
npm test
npm run migrate:local
npm run migrate:local
python3 -m unittest discover -s test -p 'test_*.py'
```

`npm test` сначала собирает Worker через `wrangler deploy --dry-run`, **без загрузки**,
затем использует workerd/Miniflare и реальный локальный SQLite-backed D1.
Все исходящие fetch перехвачены mock-обработчиком, допускающим исключительно
`getBusinessConnection` с синтетическим токеном. Тесты закрывают эмуляторы и удаляют
временные базы. Перезапуск означает полное закрытие Miniflare и открытие той же
базы новым экземпляром. Миграции CLI отдельно проверяются дважды.

В Codex задайте `XDG_CONFIG_HOME=/workspace/.cache` и
`WRANGLER_SEND_METRICS=false`; эмулятору нужны локальные loopback-сокеты.
GitHub Actions `Cloudflare bridge` выполняет те же проверки без secrets,
Cloudflare login, remote migrations и deploy. Зафиксированы зависимости и lockfile.
Для локальных тестов закреплён стабильный Miniflare 4; Wrangler имеет собственную
зависимость Miniflare 5 beta. Compatibility date обоих тестируемых Worker —
`2026-07-30`; новые runtime-возможности не требуются.

## Настройка владельцем после отдельного решения о выпуске

1. Создайте **отдельного** бота через официальный `@BotFather` → `/newbot`.
   Включите Business Mode в настройках этого бота (названия меню могут меняться).
   Сохраните токен в менеджере паролей. Никакой личной Telegram StringSession,
   кодов входа или Telethon здесь нет.
2. В Telegram владельца откройте настройки Telegram Business → чат-боты.
   Проверьте доступность функции для вашего аккаунта/подписки. Подключите нового
   бота только после настройки Worker. Ограничьте чаты и права в Telegram;
   не выдавайте права отправки, удаления, изменения профиля и управления подарками,
   если интерфейс позволяет их отключить. Даже если Telegram предоставляет более
   широкие права, код не использует их. Уточнение доступных прав требует реального UI.
3. В Cloudflare Dashboard создайте отдельную D1 database. Скопируйте её UUID.
   На своём компьютере с Node установите зависимости, выполните `npx wrangler login`.
   Скопируйте `wrangler.toml` в игнорируемый `wrangler.local.toml`; впишите UUID,
   уникальное имя Worker и свои разрешённые chat ID. `workers_dev = true` включайте
   только здесь, когда готовы открыть webhook (либо настройте свой HTTPS route).
   `preview_urls = false` и `observability.enabled = false` сохраните.
4. Только после решения о выпуске выполните на своём устройстве:

   ```sh
   npx wrangler d1 migrations apply DB --remote --config wrangler.local.toml
   npx wrangler deploy --config wrangler.local.toml
   npx wrangler secret put TELEGRAM_BOT_TOKEN --config wrangler.local.toml
   npx wrangler secret put TELEGRAM_WEBHOOK_SECRET --config wrangler.local.toml
   npx wrangler secret put TELEGRAM_OWNER_ID --config wrangler.local.toml
   ```

   Wrangler запрашивает значения интерактивно. Альтернатива — Dashboard → Worker →
   Settings → Variables and Secrets → Add → Secret. Binding DB проверьте в Bindings.
   До установки секретов endpoint возвращает 503, ничего не сохраняет.
   Не подключайте автоматический deploy из GitHub для этого PR.
5. Зарегистрируйте webhook из личного Termux по инструкции ниже. Подключите бота
   к Telegram Business с разрешёнными чатами. Отправьте новое синтетическое сообщение
   из одного явно разрешённого личного чата. После подтверждения владельца в D1
   должна появиться одна строка. Проверьте её через закрытую D1 Console Cloudflare:

   ```sql
   SELECT event_type, event_time, received_at, chat_id, message_id
   FROM events ORDER BY received_at DESC LIMIT 10;
   ```

   Это административная консоль, не публичный архив. Старые сообщения не нужны.
   Получите ID тестового чата на личном устройстве/у доверенного участника; не
   публикуйте JSON личных обновлений ради поиска ID. Не делитесь выгрузкой D1.
6. Проверьте отклонение запроса без secret, отсутствие строки для запрещённого чата,
   сохранение строки после повторного deploy той же версии с тем же DB binding.
   Результаты этих реальных проверок пока **не подтверждены**.

### Регистрация из личного Termux

Termux нужен только для стандартного Python-скрипта; Wrangler/workerd на Android
может не поддерживаться. Управление Cloudflare выполняйте через Dashboard или
поддерживаемый настольный компьютер.

```sh
pkg install python git
# Клонируйте/обновите проверенную ветку/коммит на личном устройстве.
cd aissistant/cloudflare
python scripts/register_webhook.py --personal-device
```

Скрипт спрашивает HTTPS URL, затем скрыто токен и секрет webhook, уже установленные
в Cloudflare. Не помещайте их в команды, аргументы, файлы репозитория или историю
shell. Не запускайте с `set -x`, записью терминала или в Codex. Флаг обозначает
ваше подтверждение личного устройства, а не техническую проверку его принадлежности.
Секрет можно создать в менеджере паролей как 32 случайных байта в base64url без `=`.

Подтвердите фразой `REGISTER AND DROP PENDING`. Скрипт вызывает только `setWebhook`
с `allowed_updates=["business_connection","business_message"]`, `max_connections=1`,
`drop_pending_updates=true` и secret token в JSON-теле. Это намеренно удаляет все
ожидающие обновления **нового выделенного бота**, предотвращая начальную доставку
старой очереди. Повторная регистрация снова удалит очередь: не используйте её для
обычного перезапуска. TLS проверяется, редиректы запрещены, исключения не печатаются.
Telegram при кратких сбоях может повторить уже полученное событие: его подавляет D1.

Чтобы остановить сбор, отключите Business Connection в Telegram и Worker route.
Ротацию токена/секрета выполняйте только на личном устройстве. Для удаления собранных
метаданных используйте закрытую D1 Console; публичного метода удаления нет.
В фазе 1 автоматический retention не реализован: владелец должен выбрать срок и
удалить тестовую базу после проверки, если хранение больше не нужно.

## Проверка текущих API

Проверено 2026-10-28 по первичным источникам до реализации. Сайт core.telegram.org
был недоступен из-за сетевой политики, поэтому использованы официальные исходники:

- [Telegram Bot API Client.cpp](https://github.com/tdlib/telegram-bot-api/blob/e3e9dd8e5b3d7ab8537cd5a10dc31d5ffa8f82d1/telegram-bot-api/Client.cpp):
  `JsonBusinessConnection`, `process_get_business_connection_query`, business updates,
  ограничения `setWebhook.secret_token`.
- [WebhookActor.cpp](https://github.com/tdlib/telegram-bot-api/blob/e3e9dd8e5b3d7ab8537cd5a10dc31d5ffa8f82d1/telegram-bot-api/WebhookActor.cpp):
  заголовок `X-Telegram-Bot-Api-Secret-Token`.
- [Workers fetch handler](https://developers.cloudflare.com/workers/runtime-apis/handlers/fetch/),
  [D1 Binding API](https://developers.cloudflare.com/d1/worker-api/),
  [prepared statements](https://developers.cloudflare.com/d1/worker-api/prepared-statements/),
  [migrations](https://developers.cloudflare.com/d1/reference/migrations/):
  прочитаны из официального [cloudflare-docs](https://github.com/cloudflare/cloudflare-docs/tree/998668d7590142a7db2737b48a0432e33cda9067/src/content/docs).

Проверка исходников и локального runtime не заменяет реальную доставку Telegram,
подтверждение Business-прав, ограничения аккаунта Cloudflare и удалённую миграцию D1.

## Фаза 2

План защищённого MCP только для чтения/поиска — [PHASE2.md](PHASE2.md).
Сейчас MCP и хранение текстов **не реализованы и не включены**.
