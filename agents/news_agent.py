from __future__ import annotations

from agents.base import BaseAgent


class NewsAgent(BaseAgent):
    agent_name = "news_agent"
    task = "committee_news"
    role_description = (
        "News Agent: evaluate mock news, announcement, policy, and event catalyst summaries. "
        "Do not query real news."
    )
