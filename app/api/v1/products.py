from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache.redis import get_cached_product, set_cached_product
from app.core.exceptions import ProductNotFoundError
from app.db.models import Product
from app.db.session import get_session

router = APIRouter()


class ProductResponse(BaseModel):
    product_id: int
    description: str


@router.get(
    "/products/{product_id}",
    response_model=ProductResponse,
    summary="Карточка товара",
    responses={404: {"description": "Товар не найден"}},
)
async def get_product(
    product_id: int,
    session: AsyncSession = Depends(get_session),
) -> ProductResponse:
    """
    Возвращает карточку товара. Остаток намеренно исключён, тк актуален только в момент покупки
    Первый запрос читает из БД и кэширует в Redis на 5 минут
    """
    cached = await get_cached_product(product_id)
    if cached:
        return ProductResponse(**cached)

    product = await session.get(Product, product_id)
    if product is None:
        raise ProductNotFoundError(product_id)

    data = {
        "product_id": product.product_id,
        "description": product.description,
    }
    await set_cached_product(product_id, data)
    return ProductResponse(**data)
