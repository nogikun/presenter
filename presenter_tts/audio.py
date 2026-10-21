"""PCM backends and a streaming renderer. No ML packages in the controller."""

from contextlib import contextmanager
from dataclasses import dataclass
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave

from .ssml import Mark, Pause, Speech, Style


class AudioError(RuntimeError):
    pass


@dataclass(frozen=True)
class Format:
    rate: int
    channels: int = 1

    def __post_init__(self):
        if not 8000 <= self.rate <= 192000 or self.channels not in (1, 2):
            raise AudioError(f"Invalid PCM format: {self}")

    @property
    def frame_bytes(self):
        return 2 * self.channels


def multipart(fields):
    boundary = "presenter" + uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'
                      f'\r\n\r\n{value}\r\n').encode("utf-8"))
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def read_pcm(response):
    expected = response.headers.get("Content-Length")
    count = 0
    while chunk := response.read1(4096):
        count += len(chunk)
        yield chunk
    if expected is not None and count != int(expected):
        raise AudioError("The TTS server returned a truncated response")


class Backend:
    def __init__(self, config):
        self.config = config
        self.kind = config.get("backend", "s2cpp")
        defaults = {"s2cpp": "http://127.0.0.1:3030", "voicevox": "http://127.0.0.1:50021"}
        if self.kind not in defaults:
            raise AudioError(f"Unknown backend: {self.kind}")
        self.url = config.get("url", defaults[self.kind]).rstrip("/")
        parsed = urllib.parse.urlsplit(self.url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise AudioError("TTS endpoints must use loopback HTTP for local-only synthesis")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise AudioError("Invalid TTS endpoint URL")
        self.timeout = float(config.get("timeout", 120))
        if not 1 <= self.timeout <= 600:
            raise AudioError("TTS timeout must be between 1 and 600 seconds")

    def post(self, path, data, content_type="application/json"):
        request = urllib.request.Request(self.url + path, data=data,
                                         headers={"Content-Type": content_type})
        for attempt in range(40):
            try:
                # A local voice request should not traverse an HTTP proxy from the environment.
                opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                return opener.open(request, timeout=self.timeout)
            except urllib.error.HTTPError as exc:
                # s2.cpp releases its busy flag just after the previous stream ends; wait up to 10 s.
                if exc.code == 503 and attempt < 39:
                    exc.close()
                    time.sleep(.25)
                    continue
                detail = exc.read(2048).decode("utf-8", errors="replace")
                raise AudioError(f"TTS HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise AudioError(f"Cannot reach {self.url}: {exc}") from exc

    @contextmanager
    def synthesize(self, text):
        if self.kind == "s2cpp":
            params = {"stream": True, "chunked": True, "output_format": "pcm_s16le",
                      "stream_decode_stride_frames": 4, "stream_holdback_frames": 8,
                      "stream_start_buffer_ms": 0, "max_new_tokens": 768,
                      "temperature": .8, "top_p": .8, "top_k": 30}
            params.update(self.config.get("generation", {}))
            per_char = params.pop("stream_start_buffer_ms_per_char", None)
            if per_char is not None:
                # With RTF > 1 the playback deficit grows with sentence length, so scale the head start.
                params["stream_start_buffer_ms"] = round(float(per_char) * len(text))
            # These values are a wire contract, not user-selectable generation options.
            params.update(stream=True, chunked=True, output_format="pcm_s16le")
            fields = {"text": text, "params": json.dumps(params)}
            if self.config.get("voice"):
                fields["voice"] = self.config["voice"]
            payload, content_type = multipart(fields)
            with self.post("/generate", payload, content_type) as response:
                encoding = response.headers.get("X-Audio-Encoding", "").lower()
                if encoding != "pcm_s16le":
                    raise AudioError("s2.cpp must return X-Audio-Encoding: pcm_s16le; update the server")
                try:
                    fmt = Format(int(response.headers["X-Audio-Sample-Rate"]),
                                 int(response.headers["X-Audio-Channels"]))
                except (KeyError, TypeError, ValueError) as exc:
                    raise AudioError("Missing or invalid s2.cpp PCM format headers") from exc
                yield fmt, read_pcm(response)
        else:
            speaker = int(self.config.get("speaker", 3))
            query_path = "/audio_query?" + urllib.parse.urlencode({"speaker": speaker, "text": text})
            with self.post(query_path, b"") as response:
                query = json.load(response)
            query.update(prePhonemeLength=0., postPhonemeLength=0.)
            with self.post(f"/synthesis?speaker={speaker}", json.dumps(query).encode()) as response:
                # VOICEVOX's standard route returns a completed sentence, not incremental PCM.
                data = response.read()
            try:
                with wave.open(io.BytesIO(data), "rb") as wav:
                    if wav.getsampwidth() != 2 or wav.getcomptype() != "NONE":
                        raise AudioError("VOICEVOX must return uncompressed PCM16 WAV")
                    fmt = Format(wav.getframerate(), wav.getnchannels())
                    yield fmt, iter(lambda: wav.readframes(2048), b"")
            except wave.Error as exc:
                raise AudioError(f"Invalid VOICEVOX WAV: {exc}") from exc


def filters(style, fmt):
    pitch = 2 ** (style.pitch_st / 12)
    result = []
    if style.pitch_st:
        result.extend([f"asetrate={fmt.rate * pitch:.8f}", f"aresample={fmt.rate}"])
    tempo = style.rate / pitch
    while tempo < .5:
        result.append("atempo=0.5")
        tempo /= .5
    while tempo > 2:
        result.append("atempo=2")
        tempo /= 2
    if abs(tempo - 1) > 1e-8:
        result.append(f"atempo={tempo:.8f}")
    if style.silent:
        result.append("volume=0")
    elif style.volume_db:
        result.append(f"volume={style.volume_db:.4f}dB")
    return ",".join(result)


def transform(chunks, fmt, style):
    expression = filters(style, fmt)
    if not expression:
        yield from chunks
        return
    executable = shutil.which("ffmpeg")
    if not executable:
        raise AudioError("FFmpeg is required for SSML prosody and emphasis")
    command = [executable, "-hide_banner", "-loglevel", "error", "-f", "s16le",
               "-ar", str(fmt.rate), "-ac", str(fmt.channels), "-i", "pipe:0",
               "-af", expression, "-f", "s16le", "-ar", str(fmt.rate),
               "-ac", str(fmt.channels), "pipe:1"]
    errors = []
    with tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=stderr)

        def feed():
            try:
                for chunk in chunks:
                    process.stdin.write(chunk)
            except Exception as exc:
                errors.append(exc)
            finally:
                try:
                    process.stdin.close()
                except OSError:
                    pass

        thread = threading.Thread(target=feed, daemon=True)
        thread.start()
        try:
            while chunk := process.stdout.read1(4096):
                yield chunk
            thread.join(timeout=2)
            if thread.is_alive():
                raise AudioError("FFmpeg stopped while the TTS input was still active")
            code = process.wait(timeout=5)
            if errors:
                raise AudioError(f"Audio stream failed: {errors[0]}") from errors[0]
            if code:
                stderr.seek(0)
                raise AudioError("FFmpeg failed: " + stderr.read(2048).decode(errors="replace"))
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
            thread.join(timeout=1)


def render(events, backend, output, *, play=False, force=False, filler=None, filler_after=.5):
    """filler: optional (Format, PCM16) inserted only if no speech audio arrived filler_after s in."""
    output = Path(output)
    metadata = output.with_suffix(".json")
    if not force and (output.exists() or metadata.exists()):
        raise AudioError("Output exists; choose another filename or use --force")
    if play and not shutil.which("ffplay"):
        raise AudioError("--play requires ffplay")
    if any(isinstance(e, Speech) and filters(e.style, Format(44100)) for e in events):
        if not shutil.which("ffmpeg"):
            raise AudioError("FFmpeg is required for SSML prosody and emphasis")
    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".presenter-", suffix=".wav", dir=output.parent)
    os.close(handle)
    started = time.perf_counter()
    report = {"marks": [], "segments": [], "first_speech_pcm_seconds": None}
    frames, silence_frames, fmt, player = 0, 0, None, None
    pending = []
    wav = None

    def write(data):
        nonlocal frames
        if len(data) % fmt.frame_bytes:
            raise AudioError("Incomplete PCM sample frame")
        if frames + len(data) // fmt.frame_bytes > fmt.rate * 1800:
            raise AudioError("Output exceeds the 30-minute safety limit; split the presentation")
        wav.writeframesraw(data)
        if player:
            player.stdin.write(data)
        frames += len(data) // fmt.frame_bytes

    def non_speech(event):
        nonlocal silence_frames
        if isinstance(event, Mark):
            report["marks"].append({"name": event.name, "frame": frames,
                                    "audio_seconds": frames / fmt.rate})
        elif isinstance(event, Pause):
            count = round(event.seconds * fmt.rate)
            silence_frames += count
            while count:
                n = min(count, 4096)
                write(b"\0" * (n * fmt.frame_bytes))
                count -= n

    def start(current):
        nonlocal fmt, wav, player
        fmt = current
        wav = wave.open(temporary, "wb")
        wav.setparams((fmt.channels, 2, fmt.rate, 0, "NONE", "not compressed"))
        if play:
            player = subprocess.Popen([shutil.which("ffplay"), "-loglevel", "error",
                "-nodisp", "-autoexit", "-f", "s16le", "-ar", str(fmt.rate),
                "-ch_layout", "mono" if fmt.channels == 1 else "stereo",
                "-i", "pipe:0"], stdin=subprocess.PIPE)
        for queued in pending:
            non_speech(queued)

    # The filler timer and the speech loop both write; the lock keeps their audio from interleaving.
    lock, timer, speech_started = threading.Lock(), None, False

    def play_filler():
        nonlocal silence_frames
        with lock:
            if speech_started or wav is None or filler[0] != fmt:
                return
            report["filler"] = {"start_seconds": time.perf_counter() - started,
                                "seconds": len(filler[1]) / fmt.frame_bytes / fmt.rate}
            write(filler[1])
            silence_frames += len(filler[1]) // fmt.frame_bytes  # Keep it out of rtf_speech.

    try:
        for event in events:
            if not isinstance(event, Speech):
                if fmt is None:
                    pending.append(event)
                else:
                    non_speech(event)
                continue
            segment_started = time.perf_counter()
            with backend.synthesize(event.text) as (current, chunks):
                if fmt is None:
                    start(current)
                    if filler:
                        timer = threading.Timer(
                            max(0., filler_after - (time.perf_counter() - started)), play_filler)
                        timer.daemon = True
                        timer.start()
                elif current != fmt:
                    raise AudioError("TTS format changed between speech spans")
                start_frame = frames
                remainder = b""
                for chunk in transform(chunks, fmt, event.style):
                    if chunk and report["first_speech_pcm_seconds"] is None:
                        report["first_speech_pcm_seconds"] = time.perf_counter() - started
                    remainder += chunk
                    usable = len(remainder) - len(remainder) % fmt.frame_bytes
                    if usable:
                        with lock:
                            write(remainder[:usable])
                            speech_started = True
                        remainder = remainder[usable:]
                if remainder:
                    raise AudioError("TTS ended with an incomplete PCM frame")
                if frames == start_frame:
                    raise AudioError("TTS returned no audio")
                report["segments"].append({"text": event.text, "start_frame": start_frame,
                    "end_frame": frames, "generation_seconds": time.perf_counter() - segment_started})
        if fmt is None:
            raise AudioError("No speech generated")
        if timer:
            timer.cancel()
        with lock:  # Wait out a filler write that already started.
            wav.close()
            wav = None
        elapsed = time.perf_counter() - started
        speech_seconds = (frames - silence_frames) / fmt.rate
        report.update(sample_rate=fmt.rate, channels=fmt.channels,
                      audio_seconds=frames / fmt.rate, speech_seconds=speech_seconds,
                      elapsed_seconds=elapsed, rtf_speech=elapsed / speech_seconds)
        # With --play, timings include playback pipe backpressure; they are not pure GPU benchmarks.
        report["playback_enabled"] = play
        if player:
            player.stdin.close()
            if player.wait(timeout=120):
                raise AudioError("ffplay failed")
        os.replace(temporary, output)
        metadata.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return report
    except (OSError, ValueError) as exc:
        raise AudioError(str(exc)) from exc
    finally:
        if timer:
            timer.cancel()
        with lock:
            if wav is not None:
                wav.close()
                wav = None
        if player and player.poll() is None:
            player.kill()
            player.wait()
        Path(temporary).unlink(missing_ok=True)
