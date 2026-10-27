# Переменные окружения

Приложение читает env процесса, не `.env`. Заполняйте значения только в Railway
→ **service worker → Variables**, не в чате, Git, Dockerfile, build args или командах
shell. Секретные поля запечатывайте (Seal), если эта функция доступна, и сохраняйте
резервную копию в личном менеджере секретов. Ниже перечислены все переменные,
которые использует приложение/утилиты; остальные автоматически не включают источники.

## Рабочий процесс

| Имя | Synthetic | Live | Формат / назначение |
|---|---|---|---|
| `ASSISTANT_CONFIG_TOML` | Необязательно | Рекомендуется для browser-only настройки | Полный **многострочный TOML**, без секретов. Пустая строка означает безопасные defaults. Настройки chat IDs также приватны |
| `ASSISTANT_CONFIG` | Необязательно | Альтернатива inline TOML | Путь к файлу внутри контейнера. По умолчанию `config/default.toml`. Нельзя задавать одновременно с inline TOML или `--config` |
| `SYNTHETIC_DATABASE_URL` | Необязательно | Не используется | В Docker default `sqlite:////data/synthetic.db`; для отдельного тестового PostgreSQL — reference `${{Postgres.DATABASE_URL}}` |
| `LIVE_DATABASE_URL` | Не используется | Обязательно | `sqlite:////data/secretary.db` с volume или private PostgreSQL URL. Никогда не использовать ту же БД для synthetic и live |
| `ALLOW_LIVE_READS` | Не влияет | Ровно `true` | Отдельное согласие на чтение. Без него live отказывает до создания клиентов |
| `ALLOW_AI_PROCESSING` | Не влияет | Ровно `true` | Согласие на передачу текста OpenAI и обработку |
| `OPENAI_API_KEY` | Не нужен | Обязательно, secret | Ключ проекта OpenAI с установленным бюджетом/квотой |
| `TELEGRAM_BOT_TOKEN` | Не нужен | Обязательно, secret | Токен **отдельного** бота уведомлений |
| `TELEGRAM_OWNER_CHAT_ID` | Не нужен | Обязательно | Положительный ID личного чата владельца. Не username, не ID группы. Получатель фиксирован; runtime проверяет private chat |
| `TELEGRAM_API_ID` | Не нужен | Если `telegram.enabled=true` | Положительное число из my.telegram.org |
| `TELEGRAM_API_HASH` | Не нужен | Если Telegram включён, secret | API hash приложения |
| `TELEGRAM_SESSION` | Не нужен | Если Telegram включён, secret | **Строка StringSession**, не имя файла. Получается только на личном устройстве, не сохраняется в volume/репозитории/логах |
| `TELEGRAM_PEER_HASHES` | Не нужен | Для explicit users/channels, secret | JSON: signed numeric chat ID → integer access_hash. Для обычной группы с отрицательным ID выше `-1000000000000` hash не требуется. Ключи JSON — строки, значения — числа, не строки |
| `GMAIL_CLIENT_ID` | Не нужен | Если `gmail.enabled=true` | OAuth Desktop client ID |
| `GMAIL_CLIENT_SECRET` | Не нужен | Если Gmail включён, secret | Секрет того же Desktop client |
| `GMAIL_REFRESH_TOKEN` | Не нужен | Если Gmail включён, secret | Offline refresh token с **единственным** scope `https://www.googleapis.com/auth/gmail.readonly` |

`DATABASE_URL` само приложение не читает. В Railway задайте
`LIVE_DATABASE_URL=${{Postgres.DATABASE_URL}}` либо
`SYNTHETIC_DATABASE_URL=${{Postgres.DATABASE_URL}}` в разных сервисах/БД.
`Postgres` — точное имя созданного database service; замените его при переименовании.
Не копируйте фактический DSN в переписку. Не задавайте access token Gmail вручную:
он обновляется в памяти из refresh token. Не задавайте phone/code/2FA в Railway.

## Необязательные платформенные настройки

В Dockerfile уже заданы `PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1` и
`UV_CACHE_DIR=/tmp/uv-cache`. Менять их не требуется. Worker не слушает HTTP;
`PORT`, public domain и healthcheck path не нужны. Не включайте `OPENAI_LOG=debug`,
HTTP/SQL debug, shell tracing и дампы env. Приложение подавляет сторонние логи,
но безопаснее не собирать их и на уровне платформы.

Для `ASSISTANT_CONFIG_TOML` вставляйте реальные переводы строки в **одну** переменную
Railway, не строку с буквальными `\n`. Если задан inline TOML, удалите
`ASSISTANT_CONFIG` и custom start command с `--config`: неоднозначность приводит
к безопасному отказу запуска.

## Только для обслуживания, не постоянные runtime secrets

| Имя | Команда | Назначение |
|---|---|---|
| `MIGRATION_DATABASE_URL` | `aissistant-db upgrade` / `check` | Явно выбранная БД для миграции/проверки схемы. CLI не выбирает live DB автоматически |
| `MIGRATION_SOURCE_URL` | `aissistant-db copy --confirm-offline` | SQLite-источник; открывается read-only |
| `MIGRATION_TARGET_URL` | Та же команда | Отдельная **пустая** PostgreSQL-цель; данные не сливаются и не перезаписываются |
| `TEST_POSTGRES_URL` | `pytest` | Только тестовый PostgreSQL; создаются отдельные случайные схемы. Рекомендуется вместо ручного DSN запускать `tools/test_postgres.py`, который создаёт одноразовый сервер |

Ни один из этих maintenance/test DSN не нужен для обычного Railway worker.
Начальная миграция схемы выполняется worker автоматически под его блокировкой.
Секреты и одноразовые URL в shell передавайте через доверенный локальный env manager,
без литеральных значений в истории команд. Утилита авторизации не использует эти DSN.

## v0.2

`ALLOW_TELEGRAM_ALL=true` — дополнительное runtime-согласие только для
telegram.mode=all + confirm_all=true. По умолчанию отсутствует/false. Обычные
ALLOW_LIVE_READS и ALLOW_AI_PROCESSING остаются обязательными для live.
В all peer hashes приходят из metadata в защищённую БД; StringSession остаётся
только в runtime secret и памяти. Лимиты, тарифы, исключения, private policy и
digest задаются в TOML, не новыми secret переменными.
`MIGRATION_DATABASE_URL` также используется `aissistant-db usage` для просмотра
агрегированных счётчиков без текста/DSN. [Полная инструкция](v0.2.md).
