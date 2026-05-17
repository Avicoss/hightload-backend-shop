# Полный цикл проверки: установка зависимостей, интеграционные тесты,
# подъём стека, миграции, seed, посев продуктов и запуск Locust
#
# Использование:
#   .\scripts\run_load_test.ps1               # headless: 200 users, 60s
#   .\scripts\run_load_test.ps1 -Ui           # web UI на http://localhost:8089
#   .\scripts\run_load_test.ps1 -SkipInstall  # пропустить pip install
#   .\scripts\run_load_test.ps1 -SkipTests    # пропустить интеграционные тесты

[CmdletBinding()]
param(
    [switch]$Ui,
    [switch]$SkipInstall,
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }

# $ErrorActionPreference="Stop" не ловит exit code нативных команд в PowerShell 5.1
# поэтому проверяем $LASTEXITCODE вручную после каждого вызова docker-compose/pip/etc
function Check($label) {
    if ($LASTEXITCODE -ne 0) {
        throw "$label завершился с кодом $LASTEXITCODE"
    }
}

if (-not (Test-Path ".env")) {
    throw "Не найден .env в корне проекта (см. .env.example)"
}

if (-not $SkipInstall) {
    Step "Установка зависимостей"
    pip install -r requirements.txt;     Check "pip install requirements.txt"
    pip install -r requirements-dev.txt; Check "pip install requirements-dev.txt"
}

Step "docker-compose up -d"
docker-compose up -d; Check "docker-compose up -d"

Step "Применение миграций"
docker-compose run --rm migrate; Check "alembic upgrade head"

# init-script Postgres отрабатывает ТОЛЬКО на свежем volume - на существующем
# томе shop_db_test не появится. Создаём явно, идемпотентно
Step "Создание shop_db_test (если ещё нет)"
$dbExists = docker-compose exec -T postgres psql -U shop_user -d postgres -tAc `
    "SELECT 1 FROM pg_database WHERE datname='shop_db_test'"
if (-not ($dbExists -match "1")) {
    docker-compose exec -T postgres createdb -U shop_user shop_db_test
    Check "createdb shop_db_test"
    Write-Host "shop_db_test создана" -ForegroundColor Green
} else {
    Write-Host "shop_db_test уже существует" -ForegroundColor DarkGray
}

if (-not $SkipTests) {
    Step "Интеграционные тесты"
    docker-compose --profile test run --rm test; Check "pytest"
}

Step "Seed данных и получение JWT"
$seedOutput = docker-compose exec -T api python scripts/seed.py; Check "seed.py"
$seedOutput | ForEach-Object { Write-Host $_ }

# JWT печатается последней строкой с отступом - сначала находим, потом .Trim()
$jwtLine = $seedOutput | Where-Object { $_ -match '^\s+eyJ' } | Select-Object -Last 1
if (-not $jwtLine) {
    throw "Не удалось извлечь JWT из вывода seed.py"
}
$jwt = $jwtLine.ToString().Trim()

Step "Посев продуктов для нагрузки"
docker-compose exec -T postgres psql -U shop_user -d shop_db -c `
    "INSERT INTO products (product_id, stock, description) SELECT s, 1000000, 'Load test product ' || s FROM generate_series(1, 10) s ON CONFLICT (product_id) DO UPDATE SET stock = 1000000;"
Check "INSERT products"

$env:LOAD_TEST_JWT = $jwt
Write-Host "`nLOAD_TEST_JWT установлен (длина $($jwt.Length))" -ForegroundColor Green

Step "Запуск Locust"
if ($Ui) {
    Write-Host "Web UI: http://localhost:8089" -ForegroundColor Yellow
    locust -f tests/load/locustfile_multiproduct.py --host http://localhost:8000
} else {
    locust -f tests/load/locustfile_multiproduct.py `
        --host http://localhost:8000 `
        --users 200 --spawn-rate 50 --run-time 60s `
        --headless --only-summary
}
