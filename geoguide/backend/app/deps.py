from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status


def get_db() -> Any:
    return {"status": "sqlite-available"}


def require_runtime(_: Any | None = None) -> Any:
    if _ is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing runtime context")
    return _
