from __future__ import annotations

from agents.base import BaseAgent


class CapitalAgent(BaseAgent):
    agent_name = "capital_agent"
    task = "committee_capital"
    role_description = (
        "Capital Agent: evaluate fund flow, turnover, liquidity, and volume confirmation "
        "from compressed summaries."
    )
