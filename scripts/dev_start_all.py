from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT_BOOTSTRAP = Path(__file__).resolve().parents[1]
if str(ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(ROOT_BOOTSTRAP))

from scripts._common import ROOT_DIR


def main() -> int:
    print("AI Trader Assistant local development startup")
    print("Backend URL: http://127.0.0.1:8000")
    print("Frontend URL: http://127.0.0.1:5173")
    print("real_trading_enabled=false")
    print("data_source=mock")
    print("llm=mock")

    processes = [
        subprocess.Popen([sys.executable, "scripts/dev_start_backend.py"], cwd=str(ROOT_DIR)),
        subprocess.Popen([sys.executable, "scripts/dev_start_frontend.py"], cwd=str(ROOT_DIR)),
    ]

    def stop_children(*_args) -> None:
        print("Stopping local development services...")
        for process in processes:
            if process.poll() is None:
                process.terminate()
        time.sleep(1)
        for process in processes:
            if process.poll() is None:
                process.kill()

    signal.signal(signal.SIGINT, stop_children)
    signal.signal(signal.SIGTERM, stop_children)

    try:
        while True:
            for process in processes:
                if process.poll() is not None:
                    stop_children()
                    return int(process.returncode or 0)
            time.sleep(1)
    except KeyboardInterrupt:
        stop_children()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
