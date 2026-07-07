# AI Trader Assistant

AI Trader Assistant is an AI-assisted trading research and decision-support system for short-term A-share trading scenarios.

## Current Phase

PHASE 1 - Backend foundation

This phase creates the FastAPI backend foundation, including configuration loading, logging initialization, unified response structure, global exception handling, and a health check endpoint.

No database connection, database model, quant factor, LLM Gateway, Agent, order price evaluation module, trading system, or frontend page is implemented in this phase.

## Install Dependencies

```bash
conda env create -f environment.yml
conda activate ai-stock-agent
```

## Run Tests

```bash
python -m pytest
```

## Health Check

```bash
uvicorn backend.main:app --reload
```

Then open:

```text
http://127.0.0.1:8000/health
```

## Next Phase

The next step is PHASE 2 - Database system. Do not start PHASE 2 until explicitly requested.
