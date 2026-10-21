"""Serve a narrated deck on 127.0.0.1 so player.html can switch voices, and synthesize a voice the
first time someone picks it. A file:// player cannot run synthesis; this server is what lets it."""

import functools
import http.server
import json
import os
from pathlib import Path
import re
import threading
from urllib.parse import quote, urlsplit

from . import voices
from .audio import AudioError
from .narrate import voice_outputs


class Jobs:
    """One synthesis at a time: the TTS server renders one request at a time anyway."""

    def __init__(self, synthesize):
        self.synthesize, self.lock, self.state = synthesize, threading.Lock(), {"state": "idle"}

    def start(self, name):
        with self.lock:
            if self.state["state"] == "running":
                if self.state["voice"] == name:
                    return self.state
                raise AudioError(f"Already synthesizing {self.state['voice']}; try again when it finishes")
            self.state = {"voice": name, "state": "running", "done": 0, "total": 0, "message": ""}
        threading.Thread(target=self.run, args=(name,), daemon=True).start()
        return self.state

    def run(self, name):
        def log(line):
            found = re.match(r"\[(\d+)/(\d+)\]", line)
            if found:
                self.state = {**self.state, "done": int(found[1]) - 1, "total": int(found[2]), "message": line}
        try:
            self.synthesize(name, log)
            self.state = {**self.state, "state": "done", "done": self.state.get("total", 0)}
        except Exception as exc:  # Reported to the player; the server keeps running.
            self.state = {**self.state, "state": "error", "message": str(exc)}


def voice_list(narration):
    """Registered voices plus the default one, each marked ready when its audio already exists."""
    ready = {v["name"]: v for v in voice_outputs(narration)}
    names = ["default"] + [p["name"] for p in voices.profiles()]
    names += [n for n in ready if n not in names]  # Audio whose voice profile was removed since.
    return [{"name": n, "ready": n in ready, **ready.get(n, {})} for n in names]


class Handler(http.server.SimpleHTTPRequestHandler):
    """Static files with byte ranges (audio cannot seek without them) plus a small JSON API."""

    def __init__(self, *args, narration_url, narration, jobs, **kwargs):
        self.narration_url, self.narration, self.jobs = narration_url, narration, jobs
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):  # Keep the terminal for synthesis progress.
        pass

    def local(self):
        """Only this machine's pages may call the API: checks Host (DNS rebinding) and Origin (CSRF)."""
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        origin = self.headers.get("Origin")
        return self.headers.get("Host") in allowed and (origin is None or urlsplit(origin).netloc in allowed)

    def reply(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            self.send_response(302)
            self.send_header("Location", self.narration_url + "player.html")
            self.end_headers()
        elif path == "/api/voices":
            self.reply(200, {"voices": voice_list(self.narration), "job": self.jobs.state}) if self.local() \
                else self.reply(403, {"error": "forbidden"})
        elif path == "/api/job":
            self.reply(200, self.jobs.state) if self.local() else self.reply(403, {"error": "forbidden"})
        else:
            super().do_GET()

    def do_POST(self):
        if urlsplit(self.path).path != "/api/narrate":
            return self.reply(404, {"error": "not found"})
        if not self.local():
            return self.reply(403, {"error": "forbidden"})
        try:
            name = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}").get("voice")
        except (ValueError, AttributeError):
            return self.reply(400, {"error": "Send {\"voice\": \"<name>\"}"})
        if name not in {v["name"] for v in voice_list(self.narration)}:
            return self.reply(404, {"error": f"Voice not found: {name}"})
        try:
            self.reply(202, self.jobs.start(name))
        except AudioError as exc:
            self.reply(409, {"error": str(exc)})

    def send_head(self):
        # ponytail: single ranges only ("bytes=a-b", "bytes=a-", "bytes=-n"); browsers ask for no more for media.
        found = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        path = self.translate_path(self.path)
        if not found or not os.path.isfile(path) or not (found[1] or found[2]):
            return super().send_head()
        size = os.path.getsize(path)
        start, end = (size - int(found[2]), size - 1) if not found[1] else (int(found[1]), int(found[2] or size - 1))
        start, end = max(0, start), min(end, size - 1)
        if start > end:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None
        handle = open(path, "rb")
        handle.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        self.remaining = end - start + 1
        return handle

    def copyfile(self, source, outputfile):
        if hasattr(self, "remaining"):
            while self.remaining > 0:
                chunk = source.read(min(self.remaining, 1 << 16))
                if not chunk:
                    break
                outputfile.write(chunk)
                self.remaining -= len(chunk)
        else:
            super().copyfile(source, outputfile)


def server(deck, narration, synthesize, port=0):
    """An HTTP server for the deck (slides are linked relative to the player) and the API."""
    deck, narration = Path(deck).resolve(), Path(narration).resolve()
    try:
        root = Path(os.path.commonpath([deck, narration]))
    except ValueError as exc:
        raise AudioError("Deck and narration must be on the same drive") from exc
    narration_url = "/" + "".join(f"{quote(part)}/" for part in narration.relative_to(root).parts)
    handler = functools.partial(Handler, directory=str(root), narration_url=narration_url,
                                narration=narration, jobs=Jobs(synthesize))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}{narration_url}player.html"
