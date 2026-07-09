from __future__ import annotations

from agents.base import BaseAgent


class OverseasAgent(BaseAgent):
    agent_name = "overseas_agent"
    task = "committee_overseas"
    role_description = (
        "Overseas Agent: evaluate mock overseas index, leading-stock, commodity, FX, "
        "and risk-appetite summaries. Do not query real overseas providers."
    )
