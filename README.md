# Авторизація сесій Telegram

Окремий застосунок для входу в акаунт Telegram і збереження сесії в Postgres. Незалежний від `tg_checker`: свій Docker Compose, своя БД, свої порти.

Підтримує:

- вхід телефоном і кодом
- вхід QR-кодом
- вставку готової StringSession
- список і видалення збережених сесій

Сесії в БД: таблиця `account_sessions`. `api_hash` і StringSession шифруються ключем `SESSION_ENCRYPTION_KEY` (Fernet). API не повертає секрети.

## Запуск

Потрібні Docker і Docker Compose.

```bash
copy .env.example .env
```

Згенеруйте ключі й вставте їх у `.env`:

```bash
py -c "import secrets; print(secrets.token_urlsafe(24))"
py -c "import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

Перший рядок — `API_KEY`, другий — `SESSION_ENCRYPTION_KEY`.

У `.env` також вкажіть `TELEGRAM_API_ID` і `TELEGRAM_API_HASH` з [my.telegram.org](https://my.telegram.org). У веб-формі їх вводити не потрібно: користувач логіниться телефоном/кодом (і паролем 2FA) або QR.

```bash
docker compose up --build -d
```

- **HTTPS (основний вхід):** https://localhost  
- HTTP-аліас фронтенду: http://localhost:8091  
- API напряму: http://localhost:8010  
- Документація API: http://localhost:8010/docs  

Caddy слухає `:443` і проксує UI + `/api`. За замовчуванням `PUBLIC_HOST=localhost` і `CADDY_TLS_ARGS=tls internal` (локальний сертифікат; браузер покаже попередження — для локальної перевірки можна продовжити).

Для **Telegram Mini App** потрібен публічний HTTPS з довіреним сертифікатом. У `.env`:

```env
PUBLIC_HOST=auth.example.com
CADDY_TLS_ARGS=
```

Потім `docker compose up -d caddy` (порти 80/443 мають бути відкриті з інтернету; DNS на цей хост). Let's Encrypt випуститься автоматично.

UI спрощено під Telegram Mini App: лише телефон → код → (опційно) пароль 2FA. У мініапі кнопка дії — Telegram MainButton; тема береться з Telegram. Для мініапа `API_KEY` з `.env` вшивається у фронтенд під час `docker compose build` (`VITE_API_KEY`), щоб не показувати екран ключа.

Порти вибрані так, щоб можна було одночасно тримати запущеним і `tg_checker` (8090 / 8000).

## API

Усі запити, крім `GET /api/v1/health`, потребують заголовок `X-API-Key`.

| Метод | Шлях | Опис |
| --- | --- | --- |
| `POST` | `/api/v1/auth/send-code` | Надіслати код на телефон |
| `POST` | `/api/v1/auth/resend-code` | Повторно надіслати код / SMS |
| `POST` | `/api/v1/auth/sign-in` | Увійти кодом (і паролем 2FA) |
| `POST` | `/api/v1/auth/qr` | Почати QR-вхід |
| `GET` | `/api/v1/auth/qr/{login_id}` | Статус QR |
| `POST` | `/api/v1/auth/qr/password` | Пароль 2FA для QR |
| `GET` | `/api/v1/sessions` | Список сесій |
| `POST` | `/api/v1/sessions` | Зберегти готову StringSession |
| `DELETE` | `/api/v1/sessions/{id}` | Видалити сесію |

`TELEGRAM_API_ID` / `TELEGRAM_API_HASH` задаються лише в `.env` сервера.

## Зв’язок з tg_checker

Це окремий стек. Сесії з `tg_auth` не підхоплюються `tg_checker` автоматично: різні БД. Щоб використати сесію в checker, збережіть її там окремо (той самий логін або вставка StringSession). У `tg_checker` auth-функціонал лишається як був.
