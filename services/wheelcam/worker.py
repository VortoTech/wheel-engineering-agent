import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from .storage import Store, now


class Worker:
    # Window-method builds mill and round many spline edges; ~150 s was measured on a 16-window wheel.
    def __init__(self, store: Store, timeout=300, initialization_timeout=600):
        self.store = store
        self.timeout = timeout
        self.initialization_timeout = initialization_timeout
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.process = None
        self.lock_file = None

    def start(self):
        self.lock_file = (self.store.root / ".worker.lock").open("a+")
        try:
            if os.name == "posix":
                import fcntl
                fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                import msvcrt
                self.lock_file.write("0")
                self.lock_file.flush()
                self.lock_file.seek(0)
                msvcrt.locking(self.lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            self.lock_file.close()
            self.lock_file = None
            raise RuntimeError("此工作区已有 WheelCAM 服务运行，请使用现有服务或另选数据目录。")
        with self.store.connection() as db:
            db.execute("UPDATE jobs SET status='failed',error=?,finished_at=? WHERE status='running'",
                       ("上次生成因服务中断而停止，请重新生成；已完成版本仍保留。", now()))
        self.thread.start()

    def stop(self):
        self.stopped.set()
        if self.process is not None:
            self.process.terminate()
        if self.thread.is_alive():
            self.thread.join(timeout=5)
        if self.lock_file:
            self.lock_file.close()
            self.lock_file = None

    def loop(self):
        while not self.stopped.is_set():
            with self.store.connection() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
                if row:
                    db.execute("UPDATE jobs SET status='running' WHERE id=?", (row["id"],))
            if row:
                self.run(dict(row))
            else:
                self.stopped.wait(0.3)

    def run(self, job):
        destination = self.store.root / "models" / job["id"]
        staging = self.store.root / "models" / (job["id"] + ".building")
        staging.mkdir(exist_ok=True)
        (staging / "recipe.json").write_text(job["snapshot"])
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
        error = None
        report = None
        try:
            with (staging / "build.log").open("w") as log:
                self.process = subprocess.Popen(
                    [sys.executable, "-m", "wheelcam.build", str(staging / "recipe.json"), str(staging)],
                    stdout=log, stderr=log, env=env,
                )
                try:
                    deadline = time.monotonic() + self.initialization_timeout
                    while self.process.poll() is None and not (staging / "kernel.ready").exists():
                        if time.monotonic() >= deadline:
                            raise subprocess.TimeoutExpired("CAD initialization", self.initialization_timeout)
                        if self.stopped.wait(0.2):
                            self.process.terminate()
                            break
                    code = self.process.wait(timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
                    if not (staging / "kernel.ready").exists():
                        raise ValueError("CAD 内核初始化超时，请检查安装与本机运行环境后重试。")
                    raise ValueError("实体生成超过时间限制，请减少轮辐数量或调整参数后重试。")
                if code != 0:
                    raise ValueError("这组参数未能生成有效实体，请调整参数后重试；上一版模型已保留。")
            report = json.loads((staging / "report.json").read_text())
            staging.rename(destination)
        except Exception as exc:
            error = str(exc)
        finally:
            self.process = None
        with self.store.connection() as db:
            db.execute("UPDATE jobs SET status=?,report=?,error=?,finished_at=? WHERE id=?",
                       ("failed" if error else "succeeded", json.dumps(report, ensure_ascii=False) if report else None,
                        error, now(), job["id"]))
