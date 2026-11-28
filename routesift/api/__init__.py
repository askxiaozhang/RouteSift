"""HTTP request helpers."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException


def timezone(value):
    try:
        return ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(422, "无效的 IANA 时区") from exc
