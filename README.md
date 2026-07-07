# AI Trader Assistant

AI Trader Assistant is an AI-assisted trading research and decision-support system for short-term A-share trading scenarios.

## Current Phase

PHASE 0 - Project initialization

This phase only creates the base project structure, documentation, dependency list, environment variable example, Git ignore rules, and minimal test framework.

No FastAPI business API, database model, quant factor, LLM Gateway, Agent, trading system, or frontend page is implemented in this phase.

## Install Dependencies

```bash
python -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
```

## Run Tests

```bash
python -m pytest
```

## Next Phase

The next step is PHASE 1 - Backend foundation, which should introduce the FastAPI backend foundation and a health check endpoint. Do not start PHASE 1 until explicitly requested.
