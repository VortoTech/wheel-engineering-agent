import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from .storage import Store, now
from .sf3d import collect as collect_sf3d, command as sf3d_command, prepare_input as prepare_sf3d_input


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
            db.execute("UPDATE reconstruction_jobs SET status='failed',error=?,finished_at=? WHERE status='running'",
                       ("上次视觉重建因服务中断而停止，请重新生成；已完成结果仍保留。", now()))
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
                    kind = "cad"
                else:
                    row = db.execute("SELECT * FROM reconstruction_jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
                    if row:
                        db.execute("UPDATE reconstruction_jobs SET status='running' WHERE id=?", (row["id"],))
                        kind = "reconstruction"
            if row:
                self.run(dict(row)) if kind == "cad" else self.run_reconstruction(dict(row))
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

    def run_reconstruction(self, job):
        destination = self.store.root / "reconstructions" / job["id"]
        staging = self.store.root / "reconstructions" / (job["id"] + ".building")
        staging.mkdir(exist_ok=True)
        error = None
        report = None
        try:
            image = self.store.root / "images" / f'{job["image_id"]}.jpg'
            prepared_image, preprocessing = prepare_sf3d_input(image, staging / "source.png")
            args, config = sf3d_command(prepared_image, staging)
            env = os.environ.copy()
            env["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
            numba_cache = self.store.root / ".cache" / "numba"
            numba_cache.mkdir(parents=True, exist_ok=True)
            env["NUMBA_CACHE_DIR"] = str(numba_cache)
            with (staging / "reconstruction.log").open("w") as log:
                self.process = subprocess.Popen(args, stdout=log, stderr=log, env=env, cwd=config["root"])
                try:
                    code = self.process.wait(timeout=config["timeout"])
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()
                    raise ValueError("视觉重建超过时间限制，请检查模型环境或降低纹理分辨率。")
                if code != 0:
                    raise ValueError("Stable Fast 3D 运行失败；请检查模型访问权限、依赖与任务日志。")
            _, artifact = collect_sf3d(staging)
            snapshot = json.loads(job["snapshot"])
            report = {
                "provider": job["provider"], "device": config["device"], "model": config["model"],
                "source_image_id": job["image_id"], "source_image_sha256": snapshot["image_sha256"],
                "artifact": artifact, "preprocessing": preprocessing, "usage": "visual_reference_only",
                "limitations": snapshot["limitations"],
            }
            (staging / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
            staging.rename(destination)
        except Exception as exc:
            error = str(exc)
        finally:
            self.process = None
        with self.store.connection() as db:
            db.execute("UPDATE reconstruction_jobs SET status=?,report=?,error=?,finished_at=? WHERE id=?",
                       ("failed" if error else "succeeded",
                        json.dumps(report, ensure_ascii=False) if report else None, error, now(), job["id"]))
