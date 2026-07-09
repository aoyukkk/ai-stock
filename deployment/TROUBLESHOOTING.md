# Troubleshooting

Port already in use:

- Backend uses `127.0.0.1:8000`.
- Frontend uses `127.0.0.1:5173`.
- Stop the existing process or choose another local-only port.

`npm install` fails:

- Confirm Node.js LTS is installed.
- Clear npm cache if needed.
- Re-run inside `frontend/`.

Electron download is slow:

- Retry on a stable network.
- Keep packaging optional for Phase 15; web development mode remains available.

SQLite initialization fails:

- Confirm `data/` exists.
- Run `python scripts/init_local_env.py`.
- Confirm the current user can write to the workspace.

CORS error:

- Confirm backend is running at `http://127.0.0.1:8000`.
- Confirm frontend `.env.example` points `VITE_API_BASE_URL` to the local backend.

Frontend cannot connect to backend:

- Open `http://127.0.0.1:8000/health`.
- Restart backend after backend code changes.

pytest cache permission warning:

- This warning does not block tests.
- Remove `.pytest_cache` or fix workspace permissions if you want a clean cache.

FastAPI TestClient deprecation warning:

- This warning is from the local dependency stack and does not affect Phase 15 acceptance.
