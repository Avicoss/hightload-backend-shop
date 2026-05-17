import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_optional_user_id
from app.db.session import get_session
from app.services.purchase import process_purchase

router = APIRouter()


class PurchaseRequest(BaseModel):
    # user_id необязателен если передан Bearer-токен - тогда берётся из JWT
    user_id: int | None = Field(None, gt=0, description="ID пользователя (если нет Bearer-токена)")
    product_id: int = Field(gt=0, description="ID товара")
    purchased_count: int = Field(gt=0, description="Количество единиц к покупке")


class PurchaseResponse(BaseModel):
    status: str


@router.post(
    "/purchase",
    response_model=PurchaseResponse,
    summary="Купить товар",
    responses={
        409: {"description": "Недостаточно товара на складе"},
        404: {"description": "Товар не найден"},
        403: {"description": "Пользователь неактивен"},
        401: {"description": "Невалидный или истёкший токен"},
        422: {"description": "user_id не указан ни в токене, ни в теле"},
    },
)
async def purchase(
    body: PurchaseRequest,
    # Idempotency-Key опционален - если не передан, генерируется UUID автоматически
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    jwt_user_id: int | None = Depends(get_optional_user_id),
    session: AsyncSession = Depends(get_session),
) -> PurchaseResponse:
    """
    Создаёт заказ: атомарно уменьшает запас и записывает событие в аутбокс
    user_id: из Bearer-токена (приоритет) или из тела запроса
    Idempotency-Key: из заголовка или автоматически сгенерированный UUID
    """
    user_id = jwt_user_id if jwt_user_id is not None else body.user_id
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Укажите user_id в теле запроса или передайте Bearer-токен",
        )

    key = idempotency_key or str(uuid.uuid4())

    await process_purchase(
        session=session,
        user_id=user_id,
        product_id=body.product_id,
        purchased_count=body.purchased_count,
        idempotency_key=key,
    )
    return PurchaseResponse(status="success")
