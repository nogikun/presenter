"""OpenAI-compatible speech API (POST /v1/audio/speech) in front of the local TTS server."""

from datetime import datetime
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import time

from . import voices
from .audio import AudioError, Backend, filters
from .say import segments
from .ssml import Style

# response_format -> FFmpeg output arguments and Content-Type, as in OpenAI's API.
FORMATS = {
    "mp3": (["-f", "mp3", "-c:a", "libmp3lame", "-b:a", "128k"], "audio/mpeg"),
    "opus": (["-f", "ogg", "-c:a", "libopus", "-b:a", "64k"], "audio/ogg"),
    "aac": (["-f", "adts", "-c:a", "aac", "-b:a", "128k"], "audio/aac"),
    "flac": (["-f", "flac"], "audio/flac"),
    "wav": (["-f", "wav"], "audio/wav"),
    # OpenAI's pcm is raw signed 16-bit little-endian mono at 24 kHz.
    "pcm": (["-f", "s16le", "-ar", "24000"], "audio/pcm"),
}
MODELS = ("s2-pro", "tts-1", "tts-1-hd", "gpt-4o-mini-tts")
MAX_INPUT = 4096  # OpenAI's limit for one request.
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
KEY_FILE = voices.HOME / "api-key.txt"  # A secret, so not under cache/ (which may be wiped).


def api_key(host, given=None):
    """The Bearer key clients must send, or None on loopback where only this machine connects.

    Off loopback anyone on the network could speak in a cloned voice, so a key is required:
    the given one, else one generated once and kept in <home>/api-key.txt."""
    if given:
        return given
    if host in LOCAL_HOSTS:
        return None
    if not KEY_FILE.is_file():
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_text(secrets.token_urlsafe(24), encoding="ascii")
    return KEY_FILE.read_text(encoding="ascii").strip()


class RequestError(ValueError):
    def __init__(self, message, param=None, status=400):
        super().__init__(message)
        self.param, self.status = param, status


def parse(body):
    """Validate a speech request body; returns (input, voice, response_format, speed)."""
    try:
        request = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise RequestError("The request body must be JSON")
    if not isinstance(request, dict):
        raise RequestError("The request body must be a JSON object")
    text = request.get("input")
    if not isinstance(text, str) or not text.strip():
        raise RequestError("'input' must be a non-empty string", "input")
    if len(text) > MAX_INPUT:
        raise RequestError(f"'input' is longer than {MAX_INPUT} characters", "input")
    response_format = request.get("response_format", "mp3")
    if not isinstance(response_format, str) or response_format not in FORMATS:
        raise RequestError(f"'response_format' must be one of {', '.join(FORMATS)}", "response_format")
    speed = request.get("speed", 1.0)
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not .25 <= speed <= 4:
        raise RequestError("'speed' must be a number from 0.25 to 4.0", "speed")
    voice = request.get("voice")
    return text, voice if isinstance(voice, str) else None, response_format, float(speed)


def with_voice(config, name):
    """Config for one request: a registered voice by name, else the server's default voice.

    OpenAI voice names (alloy, nova, ...) are not cloned voices, so they fall back too."""
    known = {"default"} | {p["name"] for p in voices.profiles()}
    request = dict(config)
    if name in known:
        voices.set_voice(request, name)
    return request


def encode(config, text, speed, response_format, max_chars):
    """Yield encoded audio while the text is synthesized clause by clause and piped to FFmpeg."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AudioError("FFmpeg is required to encode audio")
    backend = Backend(config)
    parts = segments(text, False, max_chars)
    ready, state = threading.Event(), {}

    def feed():
        try:
            for index, part in enumerate(parts):
                with backend.synthesize(part) as (fmt, chunks):
                    if index == 0:
                        expression = filters(Style(rate=speed), fmt)
                        state["fmt"], state["process"] = fmt, subprocess.Popen(
                            [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "s16le",
                             "-ar", str(fmt.rate), "-ac", str(fmt.channels), "-i", "pipe:0",
                             *(["-af", expression] if expression else []),
                             *FORMATS[response_format][0], "pipe:1"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
                        ready.set()
                    elif fmt != state["fmt"]:
                        raise AudioError("TTS format changed between parts")
                    for chunk in chunks:
                        state["process"].stdin.write(chunk)
        except Exception as exc:  # Reported by the reading side; a thread cannot raise to it.
            state["error"] = exc
        finally:
            ready.set()
            if "process" in state:
                try:
                    state["process"].stdin.close()
                except OSError:
                    pass

    thread = threading.Thread(target=feed, daemon=True)
    thread.start()
    ready.wait()
    if "process" not in state:
        thread.join()
        raise state.get("error") or AudioError("No speech generated")
    process = state["process"]
    try:
        while chunk := process.stdout.read1(8192):
            yield chunk
        thread.join()
        if "error" in state:
            raise state["error"]
        if process.wait():
            raise AudioError(f"FFmpeg could not encode {response_format}")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        process.stdout.close()
        thread.join(timeout=1)


def handler(config, lock, max_chars, key=None):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def authorized(self):
            if key is None:
                return True
            sent = self.headers.get("Authorization", "")
            if sent.startswith("Bearer ") and hmac.compare_digest(sent[7:].encode(), key.encode()):
                return True
            self.send_json(401, {"error": {"message": "Missing or wrong API key (Authorization: Bearer)",
                                           "type": "invalid_request_error", "param": None,
                                           "code": "invalid_api_key"}})
            return False

        def log_message(self, *args):
            pass  # One line per speech request is printed instead.

        def send_json(self, status, payload):
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_error_json(self, status, message, param=None):
            self.send_json(status, {"error": {"message": message, "type": "invalid_request_error"
                                              if status < 500 else "server_error",
                                              "param": param, "code": None}})

        def do_GET(self):
            if not self.authorized():
                return
            if self.path.split("?")[0].rstrip("/") == "/v1/models":
                self.send_json(200, {"object": "list", "data": [
                    {"id": m, "object": "model", "owned_by": "presenter-tts"} for m in MODELS]})
            else:
                self.send_error_json(404, f"Unknown path: {self.path}")

        def do_POST(self):
            if not self.authorized():
                return
            if self.path.split("?")[0].rstrip("/") != "/v1/audio/speech":
                self.send_error_json(404, f"Unknown path: {self.path}")
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 64_000:
                    raise RequestError("The request body is missing or too large")
                text, voice, response_format, speed = parse(self.rfile.read(length))
                request_config = with_voice(config, voice)
            except (RequestError, AudioError) as exc:
                self.send_error_json(getattr(exc, "status", 400), str(exc), getattr(exc, "param", None))
                return
            started = time.perf_counter()
            # s2.cpp synthesizes one request at a time, so callers queue here instead of getting 503.
            with lock:
                try:
                    audio = encode(request_config, text, speed, response_format, max_chars)
                    first = next(audio, b"")
                except AudioError as exc:
                    self.send_error_json(503, str(exc))
                    return
                self.send_response(200)
                self.send_header("Content-Type", FORMATS[response_format][1])
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                try:
                    if first:
                        self.wfile.write(b"%X\r\n%s\r\n" % (len(first), first))
                    for chunk in audio:
                        self.wfile.write(b"%X\r\n%s\r\n" % (len(chunk), chunk))
                    self.wfile.write(b"0\r\n\r\n")
                    outcome = "ok"
                except (AudioError, OSError) as exc:
                    # Headers are out; ending the connection early is the only signal left.
                    self.close_connection = True
                    outcome = f"failed: {exc}"
                finally:
                    audio.close()
            name = Path(request_config.get("voice", "default")).stem
            print(f"{datetime.now():%H:%M:%S} {name} {response_format} {len(text)} chars "
                  f"{time.perf_counter() - started:.1f}s {outcome}", file=sys.stderr)

    return Handler


def serve(config, host="0.0.0.0", port=8000, key=None):
    """Serve until Ctrl+C; off loopback every request needs the API key (see api_key)."""
    key = api_key(host, key)
    max_chars = int(config.get("say", {}).get("max_chars", 40))
    httpd = ThreadingHTTPServer((host, port), handler(config, threading.Lock(), max_chars, key))
    httpd.daemon_threads = True
    shown = "localhost" if host in ("0.0.0.0", "::") else host
    print(f"OpenAI-compatible speech API: http://{shown}:{port}/v1 (Ctrl+C stops)", file=sys.stderr)
    if host not in LOCAL_HOSTS:
        print(f"Listening on {host}: other devices use this PC's IP address.", file=sys.stderr)
    print(f"API key: {key}" if key else "API key: not required (this PC only)", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
