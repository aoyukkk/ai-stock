from __future__ import annotations

from services.ifind_shadow_service import IFindShadowService


class TailShadowAnalyzer:
    """Read-only tail-session metrics facade."""

    def __init__(self, shadow_service: IFindShadowService) -> None:
        self.shadow_service = shadow_service

    def analyze(self, stock_code: str) -> dict:
        return self.shadow_service.tail_shadow(stock_code)
