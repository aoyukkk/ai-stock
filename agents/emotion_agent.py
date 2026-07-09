from __future__ import annotations

from agents.base import BaseAgent


class EmotionAgent(BaseAgent):
    agent_name = "emotion_agent"
    task = "committee_emotion"
    role_description = (
        "Emotion Agent: evaluate market emotion, limit-up ecology, sector heat, "
        "and short-term sentiment from compressed summaries."
    )
