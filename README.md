# AI Trader Assistant

AI Trader Assistant is an AI-assisted trading research and decision-support system for short-term A-share trading scenarios.

## Current Phase

PHASE 2 - Database system

This phase creates the database foundation, including SQLAlchemy ORM metadata, database session helpers, migration documentation, and SQLite-based unit tests for local validation.

No real market data interface, data collection workflow, quant factor calculation, LLM Gateway, Agent, order price calculation, trading simulator, or frontend page is implemented in this phase.

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

## Database Tests

Database unit tests use an in-memory SQLite database. Runtime database connections are read from `DATABASE_URL`, and no real database connection is created during module import.

## Next Phase

The next step is PHASE 3 - Data source abstraction. Do not start PHASE 3 until explicitly requested.
