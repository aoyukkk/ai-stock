from __future__ import annotations

import subprocess
import signal
import sys
from pathlib import Path

import servicemanager
import win32api
import win32con
import win32event
import win32job
import win32service
import win32serviceutil


class AITraderInternalWebService(win32serviceutil.ServiceFramework):
    _svc_name_ = "AITraderInternalWeb"
    _svc_display_name_ = "AI Trader Internal Web Server"
    _svc_description_ = "Loopback-only AI Trader workbench origin for Cloudflare Tunnel."

    def __init__(self, args):
        super().__init__(args)
        self.stop_event = win32event.CreateEvent(None, 0, 0, None)
        self.process: subprocess.Popen | None = None
        self.job = _create_kill_on_close_job()

    def SvcStop(self):
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.stop_event)
        if self.process and self.process.poll() is None:
            try:
                self.process.send_signal(signal.CTRL_BREAK_EVENT)
            except (OSError, ValueError):
                pass
            try:
                self.process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                _terminate_process_tree(self.process, self.job)

    def SvcDoRun(self):
        servicemanager.LogInfoMsg("AI Trader Internal Web service starting")
        service_dir = Path(sys.executable).parent
        candidates = (
            service_dir / "ai-trader-internal-web.exe",
            service_dir.parent / "ai-trader-internal-web.exe",
        )
        executable = next((item for item in candidates if item.is_file()), candidates[0])
        self.process = subprocess.Popen(
            [str(executable)],
            cwd=executable.parent,
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        _assign_to_job(self.job, self.process.pid)
        while self.process.poll() is None:
            if win32event.WaitForSingleObject(self.stop_event, 1000) == win32event.WAIT_OBJECT_0:
                break
        if self.process.poll() not in {None, 0}:
            servicemanager.LogErrorMsg(f"AI Trader Internal Web exited with code {self.process.returncode}")
            raise RuntimeError("INTERNAL_WEB_CHILD_EXITED")


def _create_kill_on_close_job():
    job = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(job, win32job.JobObjectExtendedLimitInformation)
    info["BasicLimitInformation"]["LimitFlags"] |= win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    win32job.SetInformationJobObject(job, win32job.JobObjectExtendedLimitInformation, info)
    return job


def _assign_to_job(job, pid: int) -> None:
    process_handle = win32api.OpenProcess(
        win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE,
        False,
        pid,
    )
    try:
        win32job.AssignProcessToJobObject(job, process_handle)
    finally:
        win32api.CloseHandle(process_handle)


def _terminate_process_tree(process: subprocess.Popen, job) -> None:
    try:
        win32job.TerminateJobObject(job, 1)
        process.wait(timeout=10)
    except Exception:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=15,
        )


if __name__ == "__main__":
    if len(sys.argv) == 1:
        servicemanager.Initialize()
        servicemanager.PrepareToHostSingle(AITraderInternalWebService)
        servicemanager.StartServiceCtrlDispatcher()
    else:
        raise SystemExit(win32serviceutil.HandleCommandLine(AITraderInternalWebService))
