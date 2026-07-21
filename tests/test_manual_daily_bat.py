from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_daily_workflow_is_a_manual_double_click_bat() -> None:
    path = ROOT / "双击运行_每日工作流程.bat"

    assert path.is_file()
    content = path.read_text(encoding="utf-8")
    assert 'call "%~dp0run_midday_once.cmd"' in content
    assert 'call "%~dp0run_close_once.cmd"' in content
    assert "choice /C 12Q" in content
    assert "schtasks" not in content.lower()
    assert "powershell" not in content.lower()


def test_no_daily_windows_scheduler_management_scripts_remain() -> None:
    assert not (ROOT / "scripts" / "manage_daily_tasks.ps1").exists()
    assert not (ROOT / "scripts" / "run_daily_scheduled.py").exists()
