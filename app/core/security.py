from typing import Any

import jwt
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings

# auto_error=False - не бросает 403 при отсутствии заголовка, возвращает None
bearer_scheme = HTTPBearer(auto_error=False)


def decode_token(token: str) -> dict[str, Any]:
    """Декодирует JWT и возвращает payload. Пробрасывает 401 при любой ошибке."""
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Токен истёк",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Невалидный токен",
        )


def get_optional_user_id(
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> int | None:
    """
    Извлекает user_id из Bearer-токена, если он предоставлен
    Возвращает None если заголовка Authorization нет - эндпоинт решает,
    обязателен ли токен (может принять user_id из тела запроса)
    Если токен есть, но невалидный - бросает 401
    """
    if credentials is None:
        return None
    payload = decode_token(credentials.credentials)
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Токен не содержит sub (user_id)",
        )
    try:
        return int(user_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Токен содержит некорректный user_id",
        )
