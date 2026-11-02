"""Run the s2.cpp TTS server as a background process: start, stop and status."""

import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

from .audio import AudioError, Backend
from . import runtime
from .voices import RUNTIME, engine

PID_FILE = RUNTIME / "s2-server.pid"
LOG_FILE = RUNTIME / "s2-server.log"


def endpoint(config):
    url = urlsplit(Backend(config).url)
    return url.hostname, url.port or 80


def instances(config):
    """One config per server: [server] parallel servers on consecutive ports from `url`."""
    count = config.get("server", {}).get("parallel", 1)
    if not isinstance(count, int) or not 1 <= count <= 8:
        raise AudioError("server.parallel must be a whole number from 1 to 8")
    host, port = endpoint(config)
    host = f"[{host}]" if ":" in host else host
    return [{**config, "url": f"http://{host}:{port + i}"} for i in range(count)]


def files(index, port):
    """PID and log files; the first server keeps the original names."""
    if index == 0:
        return PID_FILE, LOG_FILE
    return PID_FILE.with_name(f"s2-server-{port}.pid"), LOG_FILE.with_name(f"s2-server-{port}.log")


def reachable(config, timeout=.5):
    try:
        with socket.create_connection(endpoint(config), timeout=timeout):
            return True
    except OSError:
        return False


def recorded_pid(pid_file=None):
    """PID of the server this tool started, if that process is still s2."""
    try:
        pid = int((pid_file or PID_FILE).read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None
    # A stale PID may belong to another program by now; verify its executable name before stopping it.
    if sys.platform == "win32":
        found = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                               capture_output=True, text=True, errors="replace").stdout
        return pid if '"s2.exe"' in found.lower() else None
    found = subprocess.run(["ps", "-p", str(pid), "-o", "comm="], capture_output=True,
                           text=True, errors="replace").stdout.strip()
    return pid if Path(found).name == "s2" else None


def log_tail(lines=8, log_file=None):
    try:
        return "\n".join((log_file or LOG_FILE).read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def start(config, wait=120.):
    """Start s2.cpp in the background (it outlives this command) and wait until it listens.

    Settings come from [server] in the config: gpu_layers (36), codec_gpu (true), threads
    (0 = s2.cpp's choice) and parallel (1). Returns True if any server was started."""
    if config.get("backend", "s2cpp") != "s2cpp":
        raise AudioError("Only the s2cpp backend can be started from here")
    started = False
    for index, one in enumerate(instances(config)):
        started = start_one(one, *files(index, endpoint(one)[1]), wait) or started
    return started


def start_one(config, pid_file, log_file, wait):
    if reachable(config):
        return False
    settings = config.get("server", {})
    host, port = endpoint(config)
    executable, model, tokenizer, env = engine()
    threads = settings.get("threads", 0)
    if not isinstance(threads, int) or threads < 0:
        raise AudioError("server.threads must be 0 (automatic) or a positive whole number")
    threads = threads or runtime.auto_threads()
    try:
        accelerator = runtime.accelerator_args(settings)
        codec = runtime.codec_arg(settings)
    except ValueError as exc:
        raise AudioError(str(exc)) from exc
    command = [str(executable), "--model", str(model), "--tokenizer", str(tokenizer),
               *accelerator, *(["--threads", str(threads)] if threads else []),
               "--server", "--host", host, "--port", str(port), codec]
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with log_file.open("ab") as log:
        detach = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
                  if sys.platform == "win32" else {"start_new_session": True})
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env,
            **detach)
    pid_file.write_text(str(process.pid), encoding="ascii")
    deadline = time.monotonic() + wait
    while not reachable(config):
        if process.poll() is not None:
            pid_file.unlink(missing_ok=True)
            raise AudioError(f"The TTS server exited during startup (see {log_file}):\n"
                             f"{log_tail(log_file=log_file)}")
        if time.monotonic() > deadline:
            raise AudioError(f"The TTS server did not open {host}:{port} within {wait:.0f} s "
                             f"(see {log_file})")
        time.sleep(.5)
    return True


def stop():
    """Stop every server this tool started. Returns False if none was running."""
    stopped = False
    for pid_file in [PID_FILE, *sorted(PID_FILE.parent.glob("s2-server-*.pid"))]:
        pid = recorded_pid(pid_file)
        pid_file.unlink(missing_ok=True)
        if pid is None:
            continue
        stopped = True
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    return stopped


def status(config):
    """One entry per configured server."""
    result = []
    for index, one in enumerate(instances(config)):
        pid_file, log_file = files(index, endpoint(one)[1])
        result.append({"url": Backend(one).url, "listening": reachable(one),
                       "pid": recorded_pid(pid_file), "log": str(log_file)})
    return result


def ensure(config):
    """Start the server for say/speak if it is down, unless [server] auto_start = false."""
    if (config.get("backend", "s2cpp") == "s2cpp" and config.get("server", {}).get("auto_start", True)
            and not all(reachable(one) for one in instances(config))):
        return start(config)
    return False
