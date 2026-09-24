"""Run local API + web workbench, terminate both on Ctrl-C."""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
python = root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
if not python.exists():
    sys.exit("请先按 README 安装 Python 依赖。")
env = os.environ.copy()
# Local WHEELCAM_* settings from the gitignored .env; the shell environment wins. Other keys in .env
# (unrelated secrets) are deliberately not passed to the servers.
dotenv = root / ".env"
if dotenv.is_file():
    for line in dotenv.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.startswith("WHEELCAM_") and key not in env:
            env[key] = value.strip()
env["PYTHONPATH"] = str(root / "services")
processes = []
exit_code = 0


def cleanup(*_):
    for process in processes:
        if process.poll() is None:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
    for process in processes:
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
try:
    processes.append(subprocess.Popen([str(python), "-m", "uvicorn", "wheelcam.app:app", "--host", "127.0.0.1", "--port", "18765"],
                                      cwd=root, env=env, start_new_session=True))
    processes.append(subprocess.Popen(["npm", "run", "dev", "--workspace", "apps/web"],
                                      cwd=root, start_new_session=True))
    print("WheelCAM 本地工作台：http://127.0.0.1:5178", flush=True)
    while all(process.poll() is None for process in processes):
        time.sleep(.5)
    exit_code = next((process.returncode for process in processes if process.returncode is not None), 1)
except KeyboardInterrupt:
    pass
finally:
    cleanup()
sys.exit(exit_code)
