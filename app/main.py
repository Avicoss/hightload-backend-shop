import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from sqlalchemy import text

from app.api.v1.products import router as products_router
from app.api.v1.purchase import router as purchase_router
from app.cache.redis import get_redis
from app.core.config import settings
from app.core.exceptions import ProblemDetail, problem_detail_handler
from app.core.logging_config import setup_logging
from app.db.session import async_session_factory, engine

# Логгер получаем на уровне модуля - handler добавится в lifespan
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Настройка логирования в точке входа, а не при импорте - не ломает pytest
    setup_logging(settings.LOG_LEVEL)
    logger.info("Запуск приложения")
    redis = get_redis()
    await redis.ping()
    yield
    logger.info("Завершение работы приложения")
    await engine.dispose()
    await redis.aclose()


app = FastAPI(
    title="Shop API",
    version="1.0.0",
    description="Высоконагруженный backend магазина — 10k RPS",
    lifespan=lifespan,
)


@app.get("/health", include_in_schema=False)
async def health() -> JSONResponse:
    """Liveness-проверка для load balancer."""
    return JSONResponse({"status": "ok"})


@app.get("/ready", include_in_schema=False)
async def ready() -> JSONResponse:
    """Readiness-проверка: PostgreSQL + Redis. Возвращает 503 если хотя бы один недоступен."""
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        await get_redis().ping()
    except Exception as exc:
        logger.warning("Readiness check failed: %s", exc)
        return JSONResponse({"status": "not ready", "error": str(exc)}, status_code=503)
    return JSONResponse({"status": "ready"})


@app.middleware("http")
async def log_errors_and_slow_requests(request: Request, call_next):
    """Единственное место логирования HTTP - 4xx (WARNING), 5xx (ERROR), slow >500ms (WARNING)."""
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start) * 1000

    extra = {
        "method": request.method,
        "path": request.url.path,
        "status_code": response.status_code,
        "duration_ms": round(duration_ms, 1),
    }

    if response.status_code >= 500:
        logger.error(
            "HTTP %d %s %s (%.1fms)",
            response.status_code, request.method, request.url.path, duration_ms,
            extra=extra,
        )
    elif response.status_code >= 400:
        logger.warning(
            "HTTP %d %s %s (%.1fms)",
            response.status_code, request.method, request.url.path, duration_ms,
            extra=extra,
        )
    elif duration_ms > 500:
        logger.warning(
            "Slow request %s %s (%.1fms)",
            request.method, request.url.path, duration_ms,
            extra=extra,
        )

    return response


# Глобальный обработчик RFC 9457 ошибок
app.add_exception_handler(ProblemDetail, problem_detail_handler)

app.include_router(purchase_router, prefix="/api/v1", tags=["Purchase"])
app.include_router(products_router, prefix="/api/v1", tags=["Products"])
