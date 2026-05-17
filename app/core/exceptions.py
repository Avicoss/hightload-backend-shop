from fastapi import Request
from fastapi.responses import JSONResponse


class ProblemDetail(Exception):
    """Базовый класс ошибок в формате RFC 9457 Problem Details."""

    def __init__(
        self,
        status: int,
        title: str,
        detail: str,
        code: str,
        type_suffix: str,
        **extra: object,
    ) -> None:
        self.status = status
        self.title = title
        self.detail = detail
        self.code = code
        self.type_suffix = type_suffix
        self.extra = extra


class InsufficientStockError(ProblemDetail):
    def __init__(self, available: int, requested: int) -> None:
        super().__init__(
            status=409,
            title="Insufficient stock",
            detail=f"Only {available} items are available, but {requested} were requested.",
            code="INSUFFICIENT_STOCK",
            type_suffix="insufficient-stock",
            available_stock=available,
            requested_count=requested,
        )


class ProductNotFoundError(ProblemDetail):
    def __init__(self, product_id: int) -> None:
        super().__init__(
            status=404,
            title="Product not found",
            detail=f"Product with id {product_id} does not exist.",
            code="PRODUCT_NOT_FOUND",
            type_suffix="product-not-found",
        )


class UserNotActiveError(ProblemDetail):
    def __init__(self, user_id: int) -> None:
        super().__init__(
            status=403,
            title="User not active",
            detail=f"User {user_id} is not active or does not exist.",
            code="USER_NOT_ACTIVE",
            type_suffix="user-not-active",
        )


async def problem_detail_handler(request: Request, exc: ProblemDetail) -> JSONResponse:
    """Обработчик всех ProblemDetail-исключений - сериализует в RFC 9457
    Логирование выполняется middleware в main.py (единственное место для HTTP-логов)
    """
    body = {
        "type": f"https://api.example.com/problems/{exc.type_suffix}",
        "title": exc.title,
        "status": exc.status,
        "detail": exc.detail,
        "code": exc.code,
        **exc.extra,
    }
    return JSONResponse(status_code=exc.status, content=body)
