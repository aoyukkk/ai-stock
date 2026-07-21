# Operator Quick Start

## System Role

AI Trader Assistant V0.3 is an AI trading assistant and virtual trading simulator. It does not perform automatic real-market trading. Human traders remain responsible for all real-market actions.

## Startup

1. Create a Python environment.
2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Install frontend dependencies:

```powershell
cd frontend
npm install
cd ..
```

4. Initialize local environment:

```powershell
python scripts/init_local_env.py
```

5. Initialize database:

```powershell
python scripts/init_db.py
```

6. Start backend:

```powershell
python scripts/dev_start_backend.py
```

7. Start frontend:

```powershell
python scripts/dev_start_frontend.py
```

8. Open:

```text
http://127.0.0.1:5173
```

## Page Order

1. Dashboard
2. System Status
3. System Config
4. Quant Scan
5. Light Screening
6. AI Committee
7. Order Price Plans
8. Virtual Trading
9. Alerts
10. Daily Review
11. Memory Console

## Safety

- Real trading is closed.
- Mock data source only.
- Mock LLM only.
- All trading operations are virtual.
- Real-market trading must be performed manually by the human trader outside this system.

## Common Issues

- Backend cannot connect: open `http://127.0.0.1:8000/health`.
- npm build warning: chunk-size warnings do not block V0.3.
- pytest cache warning: permission warning does not block tests.
- SQLite initialization: run `python scripts/init_local_env.py`.
- Electron download is slow: use browser development mode while retrying packaging.
