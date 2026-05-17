# FastAPI High-Load Shop Backend

![Python](https://img.shields.io/badge/Python-3.12-3776AB?style=flat&logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688?style=flat&logo=fastapi&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat&logo=docker&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-4169E1?style=flat&logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-7-DC382D?style=flat&logo=redis&logoColor=white)
![RabbitMQ](https://img.shields.io/badge/RabbitMQ-3.13-FF6600?style=flat&logo=rabbitmq&logoColor=white)

Высоконагруженный backend интернет-магазина на FastAPI, рассчитанный на обработку тысяч запросов в секунду.

## Стек

| Слой | Технология |
|------|-----------|
| API | FastAPI + Uvicorn (4 workers) |
| БД | PostgreSQL 16 |
| Кэш / идемпотентность | Redis 7 |
| Очередь событий | RabbitMQ 3.13 |
| ORM | SQLAlchemy 2.0 async |
| Миграции | Alembic |
| Авторизация | JWT (HS256) |

## Архитектура

```
Client
  │
  ▼
FastAPI API (4 workers)
  ├── Redis  ──── idempotency fast-path (TTL 24h)
  ├── PostgreSQL ─ атомарный UPDATE stock + INSERT outbox_event
  └── Outbox Worker
        └── RabbitMQ ── публикация событий заказов
```

**Ключевые решения:**
- **Outbox pattern** — заказ и событие сохраняются в одной транзакции, исключая потерю событий
- **Двухуровневая идемпотентность** — Redis fast-path + UNIQUE constraint в PostgreSQL как защита дублей
- **`SELECT FOR UPDATE SKIP LOCKED`** — worker без конфликтов обрабатывает очередь событий
- **Атомарный `UPDATE … RETURNING`** — списание остатка без отдельного SELECT

## Быстрый старт

```bash
# 1. Скопировать конфигурацию
cp .env.example .env
# Отредактировать .env — задать пароли и JWT_SECRET_KEY

# 2. Поднять сервисы
docker-compose up -d

# 3. Применить миграции
docker-compose run --rm migrate

# 4. Заполнить БД тестовыми данными
docker-compose exec api python scripts/seed.py
```

API будет доступен на http://localhost:8000  
Swagger UI — http://localhost:8000/docs  
RabbitMQ Management — http://localhost:15672

## API

### `POST /api/v1/purchase`

Создаёт заказ: атомарно уменьшает запас товара и публикует событие через Outbox.

**Авторизация:** Bearer JWT (опционально) — либо `user_id` в теле запроса.

```http
POST /api/v1/purchase
Content-Type: application/json
Authorization: Bearer <jwt>        # опционально
Idempotency-Key: <uuid>            # опционально, иначе генерируется автоматически

{
  "user_id": 12345,
  "product_id": 42,
  "purchased_count": 2
}
```

| Код | Описание |
|-----|----------|
| 200 | Успешная покупка (или повторный запрос с тем же `Idempotency-Key`) |
| 403 | Пользователь заблокирован |
| 404 | Товар не найден |
| 409 | Недостаточно товара на складе |
| 422 | `user_id` не указан ни в токене, ни в теле |

### `GET /api/v1/products/{product_id}`

Возвращает информацию о товаре (id, описание, остаток).

### `GET /health` / `GET /ready`

- `/health` — liveness-проверка (всегда 200)
- `/ready` — readiness-проверка (PostgreSQL + Redis, возвращает 503 при недоступности)

## Тестирование

### Автоматизированный сценарий

Один скрипт ставит зависимости, поднимает стек, применяет миграции, прогоняет
интеграционные тесты, сидит БД, извлекает JWT и запускает Locust.

**Windows (PowerShell):**

```powershell
.\scripts\run_load_test.ps1               # headless: 200 users, 60s
.\scripts\run_load_test.ps1 -Ui           # web UI на http://localhost:8089
.\scripts\run_load_test.ps1 -SkipInstall  # без pip install
.\scripts\run_load_test.ps1 -SkipTests    # без интеграционных тестов
```

**macOS / Linux (bash):**

```bash

./scripts/run_load_test.sh                # headless: 200 users, 60s
./scripts/run_load_test.sh --ui           # web UI на http://localhost:8089
./scripts/run_load_test.sh --skip-install # без pip install
./scripts/run_load_test.sh --skip-tests   # без интеграционных тестов
```

### Интеграционные тесты

```bash
docker-compose --profile test run --rm test
```

### Ручное тестирование API

```bash
# Liveness / Readiness
curl http://localhost:8000/health
curl http://localhost:8000/ready

# Успешная покупка
curl -X POST http://localhost:8000/api/v1/purchase \
     -H "Content-Type: application/json" \
     -d '{"user_id": 12345, "product_id": 42, "purchased_count": 2}'
# {"status":"success"}

# Товар не найден → 404
curl -X POST http://localhost:8000/api/v1/purchase \
     -H "Content-Type: application/json" \
     -H "Idempotency-Key: err-notfound-001" \
     -d '{"user_id": 12345, "product_id": 99999, "purchased_count": 1}'

# Недостаточно остатка → 409
curl -X POST http://localhost:8000/api/v1/purchase \
     -H "Content-Type: application/json" \
     -H "Idempotency-Key: err-stock-001" \
     -d '{"user_id": 12345, "product_id": 42, "purchased_count": 9999999}'

# Пользователь не активен → 403
curl -X POST http://localhost:8000/api/v1/purchase \
     -H "Content-Type: application/json" \
     -H "Idempotency-Key: err-user-001" \
     -d '{"user_id": 777, "product_id": 42, "purchased_count": 1}'

# Идемпотентность — два запроса, один заказ
curl -X POST http://localhost:8000/api/v1/purchase \
     -H "Content-Type: application/json" \
     -H "Idempotency-Key: idem-001" \
     -d '{"user_id": 12345, "product_id": 42, "purchased_count": 1}'
# повторный запрос с тем же ключом вернёт ответ из кеша Redis

# Карточка товара
curl http://localhost:8000/api/v1/products/42
```

Альтернативно — Swagger UI: <http://localhost:8000/docs>.

### Нагрузочный тест (Locust) — ручной запуск

```powershell
# Подготовка
docker-compose exec api python scripts/seed.py
docker-compose exec postgres psql -U shop_user -d shop_db -c `
  "INSERT INTO products (product_id, stock, description) SELECT s, 1000000, 'Load test product ' || s FROM generate_series(1, 10) s ON CONFLICT (product_id) DO UPDATE SET stock = 1000000;"

$env:LOAD_TEST_JWT = "<JWT из вывода seed.py>"

# Web UI на http://localhost:8089
locust -f tests/load/locustfile_multiproduct.py --host http://localhost:8000

# Headless (200 пользователей, 60 секунд)
locust -f tests/load/locustfile_multiproduct.py `
       --host http://localhost:8000 `
       --users 200 --spawn-rate 50 --run-time 60s `
       --headless --only-summary
```

Результат на Docker Desktop (Windows/WSL2): **~1000 RPS**.

| Сценарий | HTTP |
|---|---|
| Успешная покупка / повтор по `Idempotency-Key` | 200 |
| Невалидный / истёкший JWT | 401 |
| Пользователь не активен | 403 |
| Товар не найден | 404 |
| Недостаточно остатка | 409 |
| Нет `user_id` ни в токене, ни в теле | 422 |

## Переменные окружения

Полный список — в `.env.example`.

| Переменная | По умолчанию | Описание |
|-----------|-------------|---------|
| `POSTGRES_*` | — | Подключение к PostgreSQL |
| `REDIS_*` | — | Подключение к Redis |
| `RABBITMQ_*` | — | Подключение к RabbitMQ |
| `JWT_SECRET_KEY` | — | Секрет для подписи токенов |
| `JWT_EXPIRE_MINUTES` | `60` | Время жизни токена |
| `OUTBOX_POLL_INTERVAL` | `1.0` | Интервал опроса outbox (сек) |
| `OUTBOX_BATCH_SIZE` | `100` | Размер батча outbox worker |
| `OUTBOX_MAX_RETRIES` | `5` | Максимум попыток для события |

## Структура проекта

```
app/
├── api/v1/          # Роутеры FastAPI
├── core/            # Config, logging, security, exceptions
├── db/              # SQLAlchemy models, session
├── services/        # Бизнес-логика (purchase)
├── cache/           # Redis client
└── worker/          # Outbox worker

alembic/versions/    # Миграции БД
scripts/             # Seed-скрипты, утилиты
tests/
├── integration/     # Интеграционные тесты (pytest)
└── load/            # Нагрузочные тесты (Locust)
```

## Скрипты

| Скрипт | Назначение |
|--------|-----------|
| `scripts/seed.py` | Тестовые данные для демонстрации (user 12345, product 42) |
| `scripts/seed_scenario.py` | TRUNCATE + 2 товара по 10M для сценарного теста |
| `scripts/verify_stock.py` | Проверка инварианта остатков после сценарного теста |
| `scripts/run_load_test.ps1` / `.sh` | Полный цикл: install → docker → tests → seed → Locust |
