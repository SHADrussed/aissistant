# Текущая проверка реализации

21 октября 2026, подготовка безопасного Railway deployment. Это новая проверка
после исходных 44 тестов MVP; предыдущие ограничения уточнены по фактическим результатам.

| Проверка | Результат |
|---|---|
| Python / зависимости | Python 3.12; frozen `uv.lock`, Alembic + psycopg |
| Полный test runner с PostgreSQL 17 | 75 passed, без пропущенных PG cases |
| Schema migration | new/legacy/idempotent; отказ partial/future schema |
| SQLite → PostgreSQL copy | cursors/seen/pending/delivered сохранены; merge запрещён |
| DB server + process restart | 5 pending сохранились, доставлены, затем sent=0 |
| DB worker lock | Проверены SQLite flock / PostgreSQL advisory lock и release |
| Canary logs | Startup, worker errors, SDK debug и HTTP error body не раскрывают canary |
| Docker image | Собран текущий `aissistant:railway-safe`, TLS verification включена |
| Offline Docker + volume | 5 → 0, revision 0001_initial, db check успешен |
| Runtime permissions | UID 10001, SQLite 0600, build CA/local auth tools/dependency отсутствуют |
| Второй Docker worker / SIGTERM | Второй отказал; первый завершился с exit code 0; повтор sent=0 |
| Ruff / frozen lock / whitespace | Все проверки пройдены |
| Railway и реальные внешние API | Не проверялись, deployment не выполнялся |
| Личный GUI/auth flow | Код/защитные проверки проверены; реальная авторизация не выполнялась |

## Воспроизведение

```bash
uv sync --frozen --extra dev
uv run --frozen --extra dev python tools/test_postgres.py
uv run --frozen --extra dev ruff check .
uv run --frozen --extra dev ruff format --check .
uv lock --check
```

В этой managed-среде проверкам был предоставлен разрешённый доступ к локальным
Docker/asyncio IPC sockets: ограниченный sandbox давал EPERM/timeout до mock HTTP.
При этом сетевые обращения в canary-тесте запрещены самим тестом; используется
только MockTransport. Доступ к личным интеграциям не предоставлялся.

Postgres runner требует доступного локального Docker socket, создаёт только
одноразовые тестовые ресурсы. Без `TEST_POSTGRES_URL` обычный pytest явно пропускает
PG cases; этот результат не нужно путать с запуском runner выше.

```bash
docker build -t aissistant:railway-safe .
uv run --frozen --extra dev python tools/test_container.py
```

В managed Codex environment используется штатный Docker socket, proxy configuration
и **отдельный `Dockerfile.codex`** с временным secret публичного CA:

```bash
docker --host=unix:///var/run/docker.sock build -f Dockerfile.codex \
  --secret id=build_ca,src="$CODEX_PROXY_CERT" \
  -t aissistant:railway-safe .
```

Для read-only Docker config/filesystem buildx state можно держать в writable
`BUILDX_CONFIG=/workspace/.cache/buildx`, не заменяя Docker config/registry credentials.
В текущем прогоне host networking и отключение TLS не требовались. CA mount существует
только в RUN и не попадает в runtime image. Railway использует основной
`Dockerfile` без secret mounts: его builder отвергает такие mounts даже без значения
секрета. Не меняйте `railway.toml` на `Dockerfile.codex`. Не применяйте `--insecure`, `--trusted-host`, SSL verify=false.

Подробности, реальные пробелы проверки и действия владельца:
[deployment-audit.md](deployment-audit.md). Чужая Railway auth-сессия и реальные
ключи для этих тестов не требовались и в Codex не передавались.

Сохранённые ранее cloud install/start instructions остаются инструкциями подготовки
локального synthetic workflow, а не Railway deployment и не подтверждением публикации.

## Исправление Railway build validation — 23 октября 2026

Скриншот Railway подтвердил получение коммита, но builder отверг secret mounts
до выполнения RUN. `Dockerfile` теперь не использует mounts; для локального
Codex proxy trust выделен `Dockerfile.codex`. В Railway выбирается только основной файл.

Проверено после изменения:

- `pytest -q tests/test_deployment_files.py`: 2 passed. Проверяются выбранный Railway
  Dockerfile, отсутствие неподдерживаемых mounts и совпадение всех шагов двух файлов,
  кроме временного CA mount/env в Codex-варианте.
- `docker build --check -f Dockerfile .`: no warnings. Это локальный синтаксический
  check, не запуск builder Railway.
- `docker build -f Dockerfile.codex --secret ...`: образ собран с TLS verification.
- `tools/test_container.py --image aissistant:railway-fixed`: offline 5 → 0,
  миграция/check, UID 10001, SQLite 0600, запрет второго worker, SIGTERM и restart passed.
- Ruff, frozen lock и whitespace checks: passed. Бизнес-код worker не менялся;
  полный предыдущий PostgreSQL/API contract suite в этом исправлении не повторялся.

Успех следующего Railway deployment нужно подтвердить в его build/runtime logs.
Повтор старого failed deployment может брать прежний коммит: выбирайте новую
версию из main, с исправлением Dockerfile. Личные интеграции остаются выключенными.

## Автоматизация CI — 23 октября 2026

Добавлен workflow `.github/workflows/ci.yml`: push в main, pull request,
ручной запуск и еженедельная проверка. Локально выполнено:

- actionlint 1.7.7: workflow прошёл проверку синтаксиса и expressions.
- Полный набор с одноразовым PostgreSQL 17: **77 passed**, без skips.
- После рестарта PostgreSQL сохранены cursor и 5 pending; доставка 5 → 0 прошла.
- Ruff check/format и git diff --check: passed.
- Основной Dockerfile: build --check без предупреждений. Вариант Codex собран
  с временным CA; offline container smoke (состояние, права, lock, SIGTERM) прошёл.

Запуск самого GitHub Actions runner, production Dockerfile на GitHub и ожидание CI
в Railway локальные проверки не подтверждают. Владелец сообщил, что Railway deploy
готов; независимого доступа к его панели/логам и рабочим переменным не было.
GitHub CLI не прошёл проверку авторизации, поэтому удалённый результат Actions
нужно проверить через GitHub UI. Личные интеграции не включались.

## Отдельная тестовая отправка ботом — 27 октября 2026

Добавлен ручной `aissistant --test-notification`: фиксированный текст только
private owner через существующий BotNotifier; без загрузки конфигурации worker,
БД и чтения источников. Успех, отказ для group, HTTP failure и отсутствие token
проверены с MockTransport; реальные сокеты в тестах отправки запрещены.
Проверяется закрытие клиента и отсутствие token/traceback в выводе.

Полный локальный набор с одноразовым PostgreSQL: **81 passed**. Это не
подтверждает фактическую доставку реальным ботом: её должен проверить владелец
в Railway по [инструкции](telegram-bot.md). Личные токены в Codex не передавались,
реальные Telegram/Gmail/OpenAI не вызывались.

## v0.2 — 27 октября 2026

В feature-ветке реализованы режимы Telegram explicit/all, Gmail inbox/all_mail,
локальные privacy filters, приоритеты/digest/low request, durable API reservations
и миграция 0002_monitoring. Live и deploy не включались.

Проверено локально:

- Полный suite с одноразовым PostgreSQL 17: **142 passed**, без skips/warnings.
- Два backend: versioned v1 → v2 сохраняет cursor/seen/pending/delivered;
  SQLite → PostgreSQL copy сохраняет peer cache и budget reservations также.
- Отдельные процессы + restart PostgreSQL: pending 5 → доставка → 0, без повторов.
- Fake Telegram: двойной opt-in, bounded discovery, новые peers без истории,
  durable baselines, исключения ID/type и notification bot.
- Fake Gmail: all_mail принимает новую архивированную почту, исключает
  SENT/DRAFT/SPAM/TRASH; смена режима сохраняет historyId и seen.
- Privacy до AI: secrets, keywords, senders/chats; private local_only не
  передаёт text/canary модели и не сохраняет его в карточке.
- Бюджеты: zero/day/cycle, reservation после HTTP failure, usage/cost,
  restart без сброса, cursor не перескакивает deferred сообщение.
- Critical во время accept; grouped Important; Low только по запросу;
  персистентный часовой cap и отказ от повторов отправки в том же цикле.
- Ruff/check/format, frozen lock, whitespace и actionlint: passed.
- Production Dockerfile: build --check без предупреждений. Локальная сборка
  выполняется через эквивалентный Dockerfile.codex с временным CA proxy trust.

Реальные Telegram/Gmail/OpenAI, качество классификации, актуальные тарифы и
поведение v0.2 в Railway не проверялись. Перед выпуском нужны owner review,
решение о merge/deploy, backup и контролируемая проверка после отдельного opt-in.
Результат GitHub Actions следует оценивать по запуску конкретного commit;
локальные тесты не являются подтверждением удалённого CI.

Окончательный образ aissistant:v0.2 также прошёл tools/test_container.py:
synthetic 5 → 0, schema check 0002, права non-root/0600, отсутствие local-auth
и CA в runtime, отказ тестовой отправки без secrets, worker lock, SIGTERM/restart.

Дополнительно проверен discovery без top-message metadata: вместо cursor=0
выполняется только latest-ID baseline с возвратом без обработки истории.
