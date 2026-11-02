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


def reachable(config, timeout=.5):
    try:
        with socket.create_connection(endpoint(config), timeout=timeout):
            return True
    except OSError:
        return False


def recorded_pid():
    """PID of the server this tool started, if that process is still s2."""
    try:
        pid = int(PID_FILE.read_text(encoding="ascii").strip())
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


def log_tail(lines=8):
    try:
        return "\n".join(LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return ""


def start(config, wait=120.):
    """Start s2.cpp in the background (it outlives this command) and wait until it listens.

    Settings come from [server] in the config: gpu_layers (36) and codec_gpu (true)."""
    if config.get("backend", "s2cpp") != "s2cpp":
        raise AudioError("Only the s2cpp backend can be started from here")
    if reachable(config):
        return False
    settings = config.get("server", {})
    host, port = endpoint(config)
    executable, model, tokenizer, env = engine()
    try:
        accelerator = runtime.accelerator_args(settings)
        codec = runtime.codec_arg(settings)
    except ValueError as exc:
        raise AudioError(str(exc)) from exc
    command = [str(executable), "--model", str(model), "--tokenizer", str(tokenizer),
               *accelerator, "--server", "--host", host, "--port", str(port),
               codec]
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("ab") as log:
        detach = ({"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
                  if sys.platform == "win32" else {"start_new_session": True})
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, env=env,
            **detach)
    PID_FILE.write_text(str(process.pid), encoding="ascii")
    deadline = time.monotonic() + wait
    while not reachable(config):
        if process.poll() is not None:
            PID_FILE.unlink(missing_ok=True)
            raise AudioError(f"The TTS server exited during startup (see {LOG_FILE}):\n{log_tail()}")
        if time.monotonic() > deadline:
            raise AudioError(f"The TTS server did not open {host}:{port} within {wait:.0f} s "
                             f"(see {LOG_FILE})")
        time.sleep(.5)
    return True


def stop():
    """Stop the server this tool started. Returns False if none was running."""
    pid = recorded_pid()
    PID_FILE.unlink(missing_ok=True)
    if pid is None:
        return False
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True)
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    return True


def status(config):
    return {"url": Backend(config).url, "listening": reachable(config), "pid": recorded_pid(),
            "log": str(LOG_FILE)}


def ensure(config):
    """Start the server for say/speak if it is down, unless [server] auto_start = false."""
    if (config.get("backend", "s2cpp") == "s2cpp" and config.get("server", {}).get("auto_start", True)
            and not reachable(config)):
        return start(config)
    return False
