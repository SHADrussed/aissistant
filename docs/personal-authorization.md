# Первоначальная авторизация — только на личном устройстве

Не выполняйте этот раздел в Codex, облачной dev-среде, Railway Shell, CI, screen
sharing или с записью терминала. Не присылайте коды, пароль 2FA, session strings,
OAuth JSON, refresh tokens и снимки экрана в чат. Codex не должен их видеть.
Утилита подготовлена; её запреты запуска и отсутствие auth в worker проверены
тестами. Сам GUI и реальные OAuth/Telegram login flows ещё не выполнялись.

## Подготовка устройства

1. Используйте доверенное личное устройство с Python 3.12, uv, обычным локальным
   терминалом, браузером и Tkinter. В Linux Tkinter обычно поставляется отдельным
   пакетом `python3-tk`; на macOS/Windows проверьте поддержку Tk в установке Python.
2. Получите и проверьте исходники проекта. Из корня выполните
   `uv sync --frozen --extra local-auth`. Эта optional dependency не устанавливается
   в application image; `tools/local_authorize.py` в image не копируется.
3. Отключите историю/синхронизацию clipboard, запись экрана, crash dumps и сторонние
   clipboard managers. Буфер обмена — временный канал передачи секрета, не хранилище.
4. Утилита требует флаг `--personal-device`, интерактивный терминал и отсутствие
   переменных `CODEX_*`, `RAILWAY_*` и `CI`. Не обходите этот запрет удалением
   переменных из облачной среды. Откройте обычный локальный терминал на своём устройстве.
5. Утилита не печатает значения и не пишет secret files; выводит результат только
   в локальное маскированное окно с отдельной кнопкой копирования каждого поля.
   Clipboard очищается не позже 30 секунд, окно закрывается через 10 минут.
   Переносите значения напрямую в **ваш** браузер → Railway Variables, по одному.
   Не используйте перенаправление stdout, `tee`, shell arguments с секретами,
   `print(session)`, `credentials.to_json()` или сторонние боты для определения ID.

## Личный Telegram

1. В личном браузере откройте https://my.telegram.org → **API development tools**,
   создайте приложение, получите `api_id` и `api_hash`. Коды сайта вводите только там.
2. Локально выполните:

   ```bash
   uv run --frozen --extra local-auth python tools/local_authorize.py telegram --personal-device
   ```

3. Введите API ID/hash, номер, одноразовый код Telegram и при необходимости пароль
   2FA **только в скрытые локальные prompts**. Они не передаются Codex. Авторизация
   создаёт новую сессию вашего аккаунта; это полноценный доступ, не read-only scope.
4. Назовите только выбранные `@username` или numeric signed chat IDs. Для username
   выполняется адресное разрешение; список всех диалогов и сообщения не запрашиваются.
   Для обычной группы достаточно numeric ID. Для user/private channel без username
   потребуется access_hash, уже полученный на вашем доверенном устройстве из вашей
   разрешённой сессии. Утилита не будет обходить все диалоги ради его поиска.
   Не вводите invite URLs и не разрешайте чаты «на всякий случай».
5. Из маскированного окна перенесите API ID/hash, `TELEGRAM_SESSION` и
   `TELEGRAM_PEER_HASHES` в runtime Variables. Поле `telegram.allowed_chat_ids (TOML)`
   скопируйте в `allowed_chat_ids` секции TOML — это **не** env-переменная.
   `TELEGRAM_OWNER_CHAT_ID` понадобится для сравнения с владельцем бота.
6. Закройте окно и проверьте очистку clipboard. На устройстве не создаётся `.session`.
   В Railway пока **не** включайте `telegram.enabled` и live-разрешения.
7. В Telegram → **Settings → Devices** найдите новую сессию и убедитесь, что узнаёте
   устройство/приложение. При отмене настройки, ошибке после входа, утечке или смене
   владельца завершите именно эту сессию. Закрытие окна само по себе её не отзывает.

Рабочий процесс использует StringSession только в памяти, `receive_updates=False`,
не запускает login/start/sign_in и не перечисляет чаты. Библиотека всё равно обладает
полными правами session key: ограничения чтения реализованы в коде, а не Telegram.

## Gmail OAuth2

1. В личном браузере откройте https://console.cloud.google.com/, выберите/создайте
   проект → **APIs & Services → Library → Gmail API → Enable**.
2. В **Google Auth Platform** настройте **Branding**, **Audience** и **Data Access**
   (в старом интерфейсе — OAuth consent screen). Для личного Google account обычно
   External; добавьте себя в Test users на стадии Testing. Для Workspace Internal
   доступность зависит от вашей организации и администратора.
3. Добавьте **только** `https://www.googleapis.com/auth/gmail.readonly`.
   Не добавляйте `mail.google.com`, gmail.modify, gmail.send, openid/email/profile.
4. **Clients → Create client → Desktop app** (не Web application). Получите client ID
   и secret локально. Если интерфейс предлагает скачать JSON, не сохраняйте его
   в checkout/облачной папке/чате; предпочтительнее перенос полей из консоли напрямую.
5. Локально выполните:

   ```bash
   uv run --frozen --extra local-auth python tools/local_authorize.py gmail --personal-device
   ```

6. Введите поля только в локальные скрытые prompts. Откроется личный браузер:
   проверьте Google account, имя вашего приложения и разрешение чтения Gmail.
   Callback принимает официальный OAuth flow на loopback `127.0.0.1` со случайным
   портом и state-проверкой. Не копируйте callback URL/authorization code в Codex.
7. Из локального маскированного окна перенесите три поля `GMAIL_CLIENT_ID`,
   `GMAIL_CLIENT_SECRET`, `GMAIL_REFRESH_TOKEN` в Railway Variables. Access token
   не сохраняется в файлы и не требуется от вас в runtime settings.
8. Если ранее тому же client выдавались более широкие scopes, отзовите доступ в
   https://myaccount.google.com/connections и повторите согласие с readonly.
   Worker проверяет фактические scopes нового access token через tokeninfo.
9. External + Testing обычно даёт refresh token со сроком 7 дней для Gmail scopes.
   Перед постоянным live-развёртыванием решите требования Google к production,
   restricted scope verification/исключениям для личного использования. Переключение
   Publishing status не заменяет соблюдение политики Google и не гарантирует срок
   жизни токена. OAuth access можно отозвать в любой момент.

## Перед будущим live-включением

Нужны отдельное подтверждение владельца, проверка allowlist, отдельная live-БД,
бот из [инструкции](telegram-bot.md) и явное согласие на передачу текста OpenAI.
Ни локальная авторизация, ни заполнение секретов сами по себе не должны менять
`mode="synthetic"` или live-флаги. Сначала завершите тестовый Railway deployment.
