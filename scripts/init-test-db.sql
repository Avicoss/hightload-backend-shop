-- Создаётся Postgres-образом при первой инициализации volume (docker-entrypoint-initdb.d)
-- При уже существующем volume init-скрипты НЕ перезапускаются - run_load_test.* делает то же явно
CREATE DATABASE shop_db_test;
