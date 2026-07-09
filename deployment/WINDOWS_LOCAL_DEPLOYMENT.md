# Windows Local Deployment

This guide starts the local mock-only development build.

1. Install Python 3.11 or newer.
2. Install Node.js LTS.
3. Create and activate a virtual environment.
4. Install backend dependencies:

```powershell
pip install -r requirements.txt
```

5. Install frontend dependencies:

```powershell
cd frontend
npm install
cd ..
```

6. Initialize local folders and `.env`:

```powershell
python scripts/init_local_env.py
```

7. Confirm `.env` contains:

```text
ENABLE_REAL_TRADING=false
AI_AUTO_REAL_ORDER_ENABLED=false
```

8. Initialize or verify SQLite:

```powershell
python scripts/init_db.py
```

9. Start backend:

```powershell
python scripts/dev_start_backend.py
```

10. Start frontend:

```powershell
python scripts/dev_start_frontend.py
```

11. Open the control panel:

```text
http://127.0.0.1:5173
```

The local API listens on `http://127.0.0.1:8000`.

Common issues are covered in `TROUBLESHOOTING.md`.
