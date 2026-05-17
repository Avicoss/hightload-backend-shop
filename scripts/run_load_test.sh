#!/usr/bin/env bash
# Полный цикл проверки: установка зависимостей, интеграционные тесты,
# подъём стека, миграции, seed, посев продуктов и запуск Locust
#
# Использование:
#   ./scripts/run_load_test.sh                # headless: 200 users, 60s
#   ./scripts/run_load_test.sh --ui           # web UI на http://localhost:8089
#   ./scripts/run_load_test.sh --skip-install # пропустить pip install
#   ./scripts/run_load_test.sh --skip-tests   # пропустить интеграционные тесты

set -euo pipefail

UI=0
SKIP_INSTALL=0
SKIP_TESTS=0

for arg in "$@"; do
    case "$arg" in
        --ui)            UI=1 ;;
        --skip-install)  SKIP_INSTALL=1 ;;
        --skip-tests)    SKIP_TESTS=1 ;;
        -h|--help)
            sed -n '2,8p' "$0"
            exit 0
            ;;
        *)
            echo "Неизвестный аргумент: $arg" >&2
            exit 2
            ;;
    esac
done

cd "$(dirname "$0")/.."

step() { printf "\n\033[36m=== %s ===\033[0m\n" "$1"; }

if [[ ! -f .env ]]; then
    echo "Не найден .env в корне проекта (см. .env.example)" >&2
    exit 1
fi

if [[ "$SKIP_INSTALL" -eq 0 ]]; then
    step "Установка зависимостей"
    pip install -r requirements.txt
    pip install -r requirements-dev.txt
fi

step "docker-compose up -d"
docker-compose up -d

step "Применение миграций"
docker-compose run --rm migrate

# init-script Postgres отрабатывает ТОЛЬКО на свежем volume - на существующем
# томе shop_db_test не появится. Создаём явно, идемпотентно
step "Создание shop_db_test (если ещё нет)"
DB_EXISTS=$(docker-compose exec -T postgres psql -U shop_user -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='shop_db_test'" | tr -d '[:space:]\r')
if [[ "$DB_EXISTS" != "1" ]]; then
    docker-compose exec -T postgres createdb -U shop_user shop_db_test
    echo "shop_db_test создана"
else
    echo "shop_db_test уже существует"
fi

if [[ "$SKIP_TESTS" -eq 0 ]]; then
    step "Интеграционные тесты"
    docker-compose --profile test run --rm test
fi

step "Seed данных и получение JWT"
SEED_OUTPUT="$(docker-compose exec -T api python scripts/seed.py)"
echo "$SEED_OUTPUT"

# JWT печатается последней строкой с отступом - берём её
# (... || true) - чтобы pipefail не убил скрипт до явной проверки на пустоту
JWT="$(echo "$SEED_OUTPUT" | { grep -E '^[[:space:]]+eyJ' || true; } | tail -n 1 | xargs)"
if [[ -z "$JWT" ]]; then
    echo "Не удалось извлечь JWT из вывода seed.py" >&2
    exit 1
fi

step "Посев продуктов для нагрузки"
docker-compose exec -T postgres psql -U shop_user -d shop_db -c \
    "INSERT INTO products (product_id, stock, description) SELECT s, 1000000, 'Load test product ' || s FROM generate_series(1, 10) s ON CONFLICT (product_id) DO UPDATE SET stock = 1000000;"

export LOAD_TEST_JWT="$JWT"
printf "\n\033[32mLOAD_TEST_JWT установлен (длина %d)\033[0m\n" "${#JWT}"

step "Запуск Locust"
if [[ "$UI" -eq 1 ]]; then
    echo -e "\033[33mWeb UI: http://localhost:8089\033[0m"
    locust -f tests/load/locustfile_multiproduct.py --host http://localhost:8000
else
    locust -f tests/load/locustfile_multiproduct.py \
        --host http://localhost:8000 \
        --users 200 --spawn-rate 50 --run-time 60s \
        --headless --only-summary
fi
