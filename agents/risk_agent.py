from __future__ import annotations

from agents.base import BaseAgent


class RiskAgent(BaseAgent):
    agent_name = "risk_agent"
    task = "committee_risk"
    role_description = (
        "Risk Agent: evaluate black-swan, abnormal volatility, liquidity, T+1, and chase-risk "
        "signals. Risk may output BLOCK or WATCH_ONLY, but never executes trades."
    )
