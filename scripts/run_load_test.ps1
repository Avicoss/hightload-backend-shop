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

# 1. Установка зависимостей
if (-not $SkipInstall) {
    Step "Установка зависимостей"
    pip install -r requirements.txt
    pip install -r requirements-dev.txt
}

# 2. Подъём стека
Step "docker-compose up -d"
docker-compose up -d

# 3. Миграции
Step "Применение миграций"
docker-compose run --rm migrate

# 4. Интеграционные тесты
if (-not $SkipTests) {
    Step "Интеграционные тесты"
    docker-compose --profile test run --rm test
}

# 5. Seed основных данных + извлечение JWT
Step "Seed данных и получение JWT"
$seedOutput = docker-compose exec api python scripts/seed.py
$seedOutput | ForEach-Object { Write-Host $_ }

# JWT печатается последней строкой с отступом - берём её
$jwt = ($seedOutput | Where-Object { $_ -match '^\s+eyJ' } | Select-Object -Last 1).Trim()
if (-not $jwt) {
    throw "Не удалось извлечь JWT из вывода seed.py"
}

# 6. Посев 10 продуктов для нагрузочного теста
Step "Посев продуктов для нагрузки"
docker-compose exec postgres psql -U shop_user -d shop_db -c `
    "INSERT INTO products (product_id, stock, description) SELECT s, 1000000, 'Load test product ' || s FROM generate_series(1, 10) s ON CONFLICT (product_id) DO UPDATE SET stock = 1000000;"

# 7. Экспорт JWT
$env:LOAD_TEST_JWT = $jwt
Write-Host "`nLOAD_TEST_JWT установлен (длина $($jwt.Length))" -ForegroundColor Green

# 8. Запуск Locust
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
