# Подготовка Railway: аудит и результат проверки

Дата: 21 октября 2026. Объект — существующий MVP в `/workspace/aissistant`.
Это целевая проверка конфигурации, auth/secrets boundaries и deployment readiness,
не полный независимый security scan. Личные источники не подключались, авторизация
пользователя не выполнялась, секретные значения в Codex не запрашивались.

## Что изменено в существующем MVP

| Наблюдение | Исправление / проверка |
|---|---|
| PostgreSQL driver был, versioned migrations не было | Alembic `0001_initial`, автоматический upgrade новой БД, проверка и принятие старой схемы без переписывания данных |
| Нельзя было безопасно воспроизвести перенос состояния | Offline SQLite → пустой PostgreSQL, read-only source, одна target transaction, сверка количества и digest строк, отказ merge |
| Одна реплика в конфиге не исключала overlap при redeploy | SQLite flock / PostgreSQL session advisory lock до миграции и чтения, проверка lease connection перед отправкой; второй worker отказывает |
| Telethon по умолчанию может принимать фоновые updates | Явный `receive_updates=False`; только разрешённые InputPeer, без обхода диалогов |
| Настройка TOML в Railway browser требовала файла | `ASSISTANT_CONFIG_TOML`, отказ при неоднозначном сочетании с путём |
| Bot API wrapper полагался только на текущие места вызова | Дополнительный guard: только getChat/sendMessage и только owner ID из env |
| Документация не давала безопасный локальный auth workflow | Отдельная optional local-device утилита: guard Codex/Railway/CI/non-TTY, getpass, маскированный GUI, временный clipboard, без secret files/stdout |
| Исключения SQL могут содержать значения | Engine `hide_parameters=True`; ошибки CLI/worker по-прежнему только фиксированные события без traceback |

Бизнес-логика классификации/категорий и polling не переписана. Автоответы не добавлялись.

## Secrets/auth review

**Telegram.** Runtime принимает только env StringSession в памяти. Реальный класс
Telethon StringSession был создан с заведомо нерабочим синтетическим ключом **без
connect**, проверены `_no_updates`, отсутствие `.session` файлов и сохранение данных
только в памяти SDK. Worker не имеет login/start/sign_in/send_code_request, не читает
chats вне allowlist и не отправляет через личный аккаунт. Scope Telegram-session
всё равно полный: компрометация runtime позволяет использовать её вне этих ограничений.
Сессию нужно отзывать через Settings → Devices.

**Gmail.** Refresh token/client secret берутся только из runtime env, access token
обновляется в памяти. Только GET к profile/history/messages; фактические scopes
проверяются tokeninfo, расширенный grant отвергается. OAuth consent и loopback
authorization flow существуют только в local-device утилите, не worker/image.
OAuth refresh и tokeninfo не проверены на реальном аккаунте. Tokeninfo использует
access_token в HTTPS query, Bot API — token в HTTPS path по протоколу провайдера:
поэтому сторонние HTTP/debug/access logs нельзя включать. TLS не отключался.

**Логи.** App не печатает env/credentials/message bodies, не использует exc_info или
repr(exception); сторонние loggers/handlers подавляются при старте. Проверены canary
при ошибке конфигурации, classifier/notifier exceptions, OpenAI SDK debug и HTTP 500
с canary в error body; пользовательские токены и тексты в вывод не попадают.
За audit/agent logs хостинга, core dumps, screenshots и сторонние clipboard managers
это не даёт гарантий — ими управляет владелец платформы/личного устройства.

**Репозиторий/образ.** Secret-file patterns находятся в Git/Docker ignore; image
копирует только явные application paths. Local auth tools, optional auth dependency,
.env и `.session` в image не нужны и не включены. Проверены отсутствие build CA в
runtime, UID 10001 и права SQLite 0600. Dockerfile не читает личные runtime secrets
при build. Наличие .gitignore не защищает от ручной загрузки секретов через браузер.

**Нет автоматических ответов.** Intake Telegram использует только чтение;
Gmail scope readonly; OpenAI без tools; notifier разрешает только один private
owner. Черновики хранятся/передаются как текст карточки, не выполняются. Tests
запрещают auth/reply методы в worker и HTTP-вызов bot wrapper с другим методом
или получателем. Сам владелец должен правильно выбрать owner ID: код проверяет
private chat, но не доказывает физическую личность владельца.

## Реально проверено

- PostgreSQL 17 на отдельном одноразовом локальном Docker server, **не Railway**.
- 75 тестов с включёнными PostgreSQL cases, без пропусков. Внешние API — fake transports.
- Свежая schema, idempotent upgrade, принятие старой schema с сохранением данных,
  отказ partial schema/unknown revision, атомарность seen/outbox при сбое записи.
- SQLite → PostgreSQL: точное сохранение cursors/seen/delivered/pending, отказ
  непустой цели; исходная БД не очищается и остаётся usable.
- Отдельные процессы и реальный restart PostgreSQL: пять pending результатов и
  контрольный cursor сохранились; после доставки следующий цикл sent=0.
- DB locks для обоих backend: второй worker отказал, после release следующий стартовал.
- Docker build с предоставленным CA как временным BuildKit secret, штатным proxy,
  без host networking/обхода политики/выключения TLS verification в текущем прогоне.
- Offline Docker с persistent volume: 5 → 0, начальная migration/check в установленном
  wheel, non-root, SQLite 0600, отсутствие local-auth dependency/tools/build CA,
  запрет второго процесса, SIGTERM exit 0, сохранение дедупликации после остановки.
- `ruff check`, `ruff format --check`, `uv lock --check`, `git diff --check`.

Команды воспроизведения и версия схемы: [validation.md](validation.md).

## Не проверено

- Создание service, build, database reference/private DNS, volume mount/permissions,
  restart и functional logs **в самой Railway**. Нет авторизованного Railway
  connector/browser/account в этой сессии; Railway token не запрашивался.
- Реальные Telegram MTProto, Gmail OAuth refresh/history, OpenAI, Bot API delivery,
  quotas, account consent и реальный allowlist. Конструктор SDK/MockTransport —
  не доказательство работы личной интеграции.
- GUI Tkinter, фактический loopback OAuth consent и Telegram login на вашем личном
  устройстве; offline guard/tests не заменяют эту проверку.
- Provider backups, TLS внешнего PostgreSQL, restore в новом Railway project,
  uptime/alerting/billing, adversarial нагрузка и масштабирование.

## Что делает владелец и что блокирует deployment

1. В checkout нет первого коммита; `HEAD` не разрешается. Read-only проверка origin
   не нашла `refs/heads/main`. Сначала сохраните/review/commit/push код в нужную
   приватную ветку. Пока этого нет, GitHub-based Railway deploy нечего собирать.
   В этой задаче code push/PR не создавались.
2. Создайте Railway project/service/storage через
   [пошаговую browser-инструкцию](railway.md), оставив **только synthetic**.
   Проверьте два цикла и cold restart на платформе. Доступ к вашей Railway сессии
   Codex не предоставлен, поэтому этот пункт остаётся внешним блокером подтверждения
   именно Railway-ready статуса, не выявленной ошибкой кода.
3. Для live позже: выполните [личную авторизацию](personal-authorization.md) и
   [настройку отдельного бота](telegram-bot.md), заполните runtime secrets по
   [точному списку env](environment-variables.md). Секреты вводятся только у вас,
   не в чате. Проверьте Gmail OAuth token lifetime и сетевой MTProto доступ.
4. Только после отдельного подтверждения включать live в отдельной БД, по одному
   источнику, с контролируемым новым сообщением. Настройте backups/alerts/retention.

**Вывод:** в проверенном локальном synthetic workflow кодовых блокеров не выявлено.
Проверенный Railway deployment пока отсутствует: нужны опубликованный код и ваши
действия в Railway. Live дополнительно заблокирован отсутствием авторизации,
подтверждения владельца и проверки реальных интеграций — эти ограничения намеренные.
Exactly-once delivery не обещается; при неопределённом send/commit возможен дубль
карточки. MVP остаётся single-owner/single-worker, не многорепличным сервисом.
