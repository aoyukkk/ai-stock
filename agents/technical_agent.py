from __future__ import annotations

from agents.base import BaseAgent


class TechnicalAgent(BaseAgent):
    agent_name = "technical_agent"
    task = "committee_technical"
    role_description = (
        "Technical Agent: evaluate trend, volume-price structure, short-term strength, "
        "and technical pattern quality from compressed summaries."
    )
