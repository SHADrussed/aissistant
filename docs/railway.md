# Railway: безопасная настройка через браузер

Текущий статус: исходники опубликованы в GitHub; на предоставленном скриншоте
Railway успешно получил коммит, но отклонил прежний Dockerfile при validation.
Причина — `RUN --mount=type=secret`, который Railway builder не принимает.
Основной `Dockerfile` исправлен: secret mounts удалены, TLS verification сохранена.
Для Codex с собственным TLS-прокси есть отдельный `Dockerfile.codex`; Railway должен
выбирать **только `Dockerfile`**, как указано в `railway.toml`.

Успешная полная сборка/работа на Railway ещё требует проверки нового deployment.
Личные интеграции включать только после отдельного подтверждения владельца.

[Полный список env](environment-variables.md) · [Личная авторизация](personal-authorization.md) ·
[Отдельный бот](telegram-bot.md) · [Миграции](database-migrations.md) · [Аудит](deployment-audit.md)

## 1. Подготовить исходники

1. Проверьте изменения локально, сохраните их Git-коммитом и отправьте в **приватный**
   GitHub repository/ветку, которую вы выберете в Railway. `main` уже создана и
   опубликована; новый deploy должен брать последний коммит с исправлением Dockerfile.
2. `.gitignore` защищает Git workflow, но не ручную загрузку файлов через GitHub UI.
   Не загружайте `.env`, `.data`, `.venv`, local config, `.session`, OAuth JSON,
   SQL dumps, clipboard screenshots и credentials. Не перетаскивайте весь checkout
   вслепую в Upload files. Нужны source/tests/docs/tools/config defaults, lockfile,
   pyproject, Dockerfile, entrypoint, railway.toml и ignore files.
3. Основной код не требует Railway CLI. GUI/локальные утилиты авторизации будут
   нужны только позже, на личном устройстве, не в облачном терминале.

## 2. Создать **synthetic** service

1. Откройте https://railway.com/dashboard → **New Project → Deploy from GitHub repo**.
   При запросе GitHub доступа разрешите Railway только нужный приватный repository.
   Выберите repository и ветку с проверенным кодом.
2. Назовите worker, например `aissistant-synthetic`. В **Settings → Source** проверьте
   branch и **Root Directory**. Если файлы лежат в корне Git repository — оставьте
   корень, не `/workspace/aissistant` (это путь Codex, не Railway).
3. `railway.toml` выбирает Dockerfile и `numReplicas=1`. В **Settings → Build/Deploy**
   проверьте Dockerfile builder и путь `Dockerfile` (не `Dockerfile.codex`),
   одну реплику в одном регионе, restart on failure
   с лимитом 5. Не запускайте несколько services на одной рабочей БД.
4. **Custom Start Command** оставьте пустым: используются Docker ENTRYPOINT/CMD.
   **Healthcheck Path** пустой, public domain не создавайте. Это постоянный worker,
   а не web service и не cron job. Serverless/App Sleeping выключите, если доступны.
5. Первый deploy с defaults может запуститься сразу. Он безопасно synthetic и не
   делает внешних API-вызовов, но **без persistent storage состояние SQLite временное**.
   До теста перезапуска завершите следующий раздел.

Названия вкладок могут отличаться в интерфейсе Railway; проверяйте смысл поля.
Статус Active сам по себе означает только живой процесс, не функциональную проверку.

## 3. Выбрать persistent storage — один из вариантов

### Вариант A: SQLite, проще для первого запуска

1. В проекте добавьте **Volume** и привяжите его к synthetic worker; mount path `/data`.
   В интерфейсе это может быть **service → Settings → Volumes → Add Volume** или
   **New → Volume → Attach to service**. Не выбирайте временный filesystem.
2. В **Variables** добавьте `SYNTHETIC_DATABASE_URL` со значением
   `sqlite:////data/synthetic.db`. Оно не секретное.
3. Entrypoint готовит корень `/data`, затем приложение запускается с UID/GID 10001.
   Перенесённые существующие файлы должны быть доступны этому UID. При нестандартной
   non-root политике платформы volume нужно подготовить с этими правами отдельно.
4. Не добавляйте Postgres reference и не меняйте `LIVE_DATABASE_URL` для этого теста.

### Вариант B: отдельный тестовый PostgreSQL

1. В проекте **New → Database → Add PostgreSQL**. Дождитесь готовности database service;
   для примера назовите его `Postgres`. Это только тестовая БД, без личных данных.
2. В worker → **Variables** добавьте reference variable:

   ```text
   SYNTHETIC_DATABASE_URL=${{Postgres.DATABASE_URL}}
   ```

   Через UI можно выбрать **Add Reference → Postgres → DATABASE_URL**. Если сервис
   назван иначе, подставьте его точное имя. Не копируйте пароль/фактический DSN в чат.
3. Используйте direct private database connection, не PgBouncer transaction pooler:
   блокировка worker привязана к серверной сессии. Приложение читает именно
   `SYNTHETIC_DATABASE_URL`, не просто `DATABASE_URL`.
4. Volume `/data` worker в этом варианте не требуется: состояние лежит в PostgreSQL.
   Private network/DNS Railway доступны runtime, а не обычному build step. Не делайте
   database queries в Docker build; этот Dockerfile их не выполняет.
5. Не включайте публичный TCP proxy к БД для работы worker. Для внешнего PostgreSQL
   по публичной сети требуется подтверждённая TLS-конфигурация с проверкой hostname/CA.
   Не добавляйте `sslmode=disable` как обход ошибки. Встроенная Railway private network
   и публичный TLS endpoint — разные режимы; default DB не обязательно имеет публичный TLS.

## 4. Настроить быстрый synthetic smoke test

В worker → **Variables** создайте **одну многострочную** переменную
`ASSISTANT_CONFIG_TOML` и вставьте:

```toml
mode = "synthetic"
poll_seconds = 15

[telegram]
enabled = false
allowed_chat_ids = []

[gmail]
enabled = false
```

Вставляйте TOML в **Value одной переменной**, не как отдельные строки env в Raw Editor.
Остальные поля используют defaults (все дни/часы). Удалите `ASSISTANT_CONFIG`, если
раньше его добавили: одновременно использовать inline TOML и путь запрещено.

**Не добавляйте** `ALLOW_LIVE_READS`, `ALLOW_AI_PROCESSING`, OpenAI key, Telegram
session/API/bot credentials и Gmail OAuth для synthetic. Они ему не нужны.
Выберите **Apply/Deploy Changes** для накопленных изменений.

## 5. Проверить логи и redeploy в Railway

1. Откройте worker → **Deployments → текущий deploy → View Logs** (build/runtime).
   Убедитесь, что сборка успешна и runtime содержит:

   ```text
   database_ready backend=sqlite revision=0001_initial
   cycle_complete sent=5 success=True
   ```

   Для PostgreSQL будет `backend=postgresql`. На непустой synthetic БД первый счётчик
   может быть уже 0 — это нормально, если пять fixtures были обработаны ранее.
2. Через 15 секунд после завершения цикла ожидайте `sent=0 success=True`.
   **Это не пять сообщений в Telegram:** synthetic notifier лишь валидирует карточки
   локально. Не должно быть запросов личной авторизации, текстов сообщений или токенов.
3. В **Deployments** вручную остановите текущий worker (**Stop/Remove deployment**,
   не Delete service/volume/database). Затем используйте **Redeploy/Deploy** для той
   же версии и переменных. Не удаляйте state. Если UI не позволяет cold restart,
   обычный Redeploy может кратко пересечься со старым процессом: lock остановит второй;
   дождитесь остановки старого и повторите Redeploy.
4. После restart ожидайте тот же `revision` и `sent=0`. Если снова 5 — состояние
   потеряно/сменился URL: до live разберитесь с volume mount или DB reference.
5. Проверьте один worker/одну реплику и отсутствие `startup_or_storage_failed...`,
   `source_poll_failed`, `notification_failed` и `success=False`.
6. Сохраните у себя только обезличенный результат проверки (deploy ID, тип БД,
   revision, счётчики). Не экспортируйте весь env или логи с чужими данными.
7. После smoke test замените `poll_seconds=15` на `900`, Apply/Deploy; снова проверьте
   `sent=0`. Оставьте synthetic до отдельного решения о live.

Проверка **pending** очереди при сбое доставки и перезапуске PostgreSQL выполнена
локальным test runner, не Railway synthetic notifier. На платформе шаги выше
проверяют подключение storage, build, loop и дедупликацию после restart. Их нельзя
выдать за проверку реальных API или отказоустойчивости Railway PostgreSQL.

## 6. Позже: подготовить live без включения чтения

Создайте отдельный `aissistant-live` service и отдельный volume/DB. Не меняйте
synthetic БД на личную и не соединяйте два worker с одной БД. Сохраните
`mode="synthetic"` и выключенные источники до согласованного переключения.

После локальной авторизации можно внести секреты через Variables (по таблице
[env](environment-variables.md)), но до подтверждения не ставить live mode и оба
ALLOW-флага. Railway может применять переменные с redeploy, поэтому проверяйте,
что в сохранённой конфигурации всё ещё synthetic. Не открывайте эти Variables
в Codex browser/screen sharing; данная задача их не требует.

**Только после отдельного подтверждения владельцем** в отдельной live-БД задаются
`LIVE_DATABASE_URL`, оба ALLOW=`true` и inline TOML, например:

```toml
mode = "live"
poll_seconds = 900

[telegram]
enabled = true
allowed_chat_ids = [-123] # ЗАМЕНИТЬ на реальные выбранные ID с личного устройства

[gmail]
enabled = false # Включать отдельно; нужны readonly OAuth credentials
label_ids = ["INBOX"]
```

Этот пример **не применять сейчас**; numeric ID вымышленный, не рабочий allowlist.
Включайте по одному источнику. Первый цикл создаёт baseline и не анализирует
историю, следующий проверяется вашим контролируемым новым сообщением. Проверьте
получателя-бота и отсутствие ответа собеседнику. Включение второго источника —
отдельный осознанный шаг после проверки первого.

## 7. Эксплуатационные условия и блокеры live

- HTTPS egress: `api.openai.com`, `api.telegram.org`, `gmail.googleapis.com`,
  `oauth2.googleapis.com`. Google browser consent (`accounts.google.com`) нужен на
  личном устройстве. Telethon также требует MTProto TCP к Telegram DC — это не Bot API.
  Его доступность в Railway ещё не проверялась, и один allowlist api.telegram.org
  недостаточен. Сетевую политику Codex не обходим и личную сессию здесь не проверяем.
- Telegram StringSession хранится только в runtime secret env и памяти SDK, не в БД
  или volume. Refresh token и Bot token требуют того же контроля доступа/ротации.
- Gmail External Testing и 7-дневный refresh token — ограничение для постоянного
  deployment, не ошибка контейнера. Выполните требования Google перед обещанием
  непрерывной работы. Удалённый/истёкший historyId требует ручного решения владельца.
- Backups, retention и проверка восстановления должны быть настроены отдельно.
  Сводки и черновики в outbox — личные данные. Логическое удаление не стирает backups.
- Дайте до 120 секунд graceful shutdown, если настройки платформы позволяют; иначе
  hard kill безопасен для committed state, но не исключает повтор карточки между send
  и commit. Нет гарантии exactly-once. Полный цикл может занять больше grace period.
- Логи фиксированных событий не заменяют alerting; следите за отсутствием успешных
  циклов, error-кодами, quota/стоимостью, размером pending и свободным storage.

Локальный успех не подтверждает billing/квоты Railway, Docker build на его builder,
private DNS/volume permissions, UI workflow, внешние API или реальные пользовательские
согласия. Этот документ — процедура проверки владельцем, а не отчёт о её выполнении.
