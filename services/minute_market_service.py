from __future__ import annotations

from services.ifind_shadow_service import IFindShadowService


class MinuteMarketService:
    """Named facade for on-demand minute Shadow data; it has no quant/order hooks."""

    def __init__(self, shadow_service: IFindShadowService) -> None:
        self.shadow_service = shadow_service

    def load(self, stock_code: str, start_time: str, end_time: str) -> dict:
        return self.shadow_service.load_minute(stock_code, start_time, end_time)

    def tail_shadow(self, stock_code: str) -> dict:
        return self.shadow_service.tail_shadow(stock_code)
