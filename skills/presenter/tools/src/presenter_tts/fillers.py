"""Optional hesitation fillers for `say`: えーと etc. in the current voice, played only when the
first audio is late. Self-contained: deleting this file and the `Fillers` lines in say.py removes
the feature; render()'s late-clip hook and voices.FILLER_CACHE work without it."""

from array import array
from pathlib import Path
import random
import sys
import wave

from . import voices
from .audio import AudioError, Format, transform
from .ssml import Style

# Neutral hesitations, plain and with Fish Audio S2 inline tone tags. "そうですね" was dropped
# (it can sound like agreement), and so was "うーん、" (the model ran on for 11 s in a test).
FILLERS = ("えーと、", "えっと、", "えーっと、", "あのー、", "[thinking] えーと、",
           "[hesitant] えっと、", "[slowly] えーっと、", "[softly] あのー、", "[inhale] えっと、")


def trim_and_fade(pcm, fmt, fade_ms):
    """Cut leading/trailing near-silence from PCM16, then fade both ends in and out."""
    samples = array("h", pcm)
    loud = [i for i, s in enumerate(samples) if abs(s) > 500]
    if not loud:
        return b""
    ch = fmt.channels
    samples = samples[loud[0] - loud[0] % ch:loud[-1] - loud[-1] % ch + ch]
    count = len(samples) // ch
    n = min(round(fade_ms * fmt.rate / 1000), count // 2)
    for f in range(n):
        gain = (f + 1) / (n + 1)
        for c in range(ch):
            samples[f * ch + c] = int(samples[f * ch + c] * gain)
            samples[(count - 1 - f) * ch + c] = int(samples[(count - 1 - f) * ch + c] * gain)
    return samples.tobytes()


def filler_takes(backend):
    """Cached filler WAVs for the current voice, synthesizing missing ones once (runaways too,
    so they are not retried; filler_pool() skips them by length)."""
    folder = voices.FILLER_CACHE / Path(backend.config.get("voice", "default")).stem
    paths = []
    for text in FILLERS:
        path = folder / f"{text}.wav"
        if not path.is_file():
            print(f"preparing filler: {text}", file=sys.stderr)
            with backend.synthesize(text) as (fmt, chunks):
                pcm = b"".join(chunks)
            folder.mkdir(parents=True, exist_ok=True)
            with wave.open(str(path), "wb") as clip:
                clip.setparams((fmt.channels, 2, fmt.rate, 0, "NONE", "not compressed"))
                clip.writeframes(pcm)
        paths.append(path)
    return paths


def filler_pool(backend, settings):
    """[(path, (Format, PCM16))] for every take within filler_max_seconds, each with a fresh
    random pitch and speed so repeats do not sound identical."""
    max_seconds = float(settings.get("filler_max_seconds", 1.5))
    pitch = float(settings.get("filler_pitch_semitones", 1.0))
    pool = []
    for path in filler_takes(backend):
        with wave.open(str(path), "rb") as clip:
            fmt = Format(clip.getframerate(), clip.getnchannels())
            pcm = trim_and_fade(clip.readframes(clip.getnframes()), fmt, 0)
        if not pcm or len(pcm) > max_seconds * fmt.rate * fmt.frame_bytes:
            continue
        style = Style(rate=random.uniform(float(settings.get("filler_rate_min", .85)),
                                          float(settings.get("filler_rate_max", 1.1))),
                      pitch_st=random.uniform(-pitch, pitch))
        pcm = b"".join(transform(iter([pcm]), fmt, style))
        pool.append((path, (fmt, trim_and_fade(pcm, fmt, int(settings.get("filler_fade_ms", 30))))))
    return pool


def pick_filler(pool, room, last=None):
    """A (path, clip) lasting at most `room` seconds and not `last`: random among the three longest
    such, so the wait is filled without always using the same longest take; None if none fits."""
    def seconds(clip):
        return len(clip[1]) / clip[0].frame_bytes / clip[0].rate
    fits = sorted((f for f in pool if f[0] != last and seconds(f[1]) <= room),
                  key=lambda f: seconds(f[1]), reverse=True)
    return random.choice(fits[:3]) if fits else None


class Fillers:
    """Filler state for one `say` session: the prepared pool (built before a line arrives, so
    choosing adds no latency) and the take heard last."""

    def __init__(self, backend, settings):
        self.backend, self.settings = backend, settings
        self.after = int(settings.get("filler_after_ms", 1000)) / 1000
        self.pool, self.last, self.chosen = [], None, None
        self.prepare()

    def prepare(self):
        """(Re)build the pool for the current voice, drawing fresh pitch and speed."""
        try:
            self.pool = filler_pool(self.backend, self.settings)
        except AudioError as exc:
            self.pool = []
            print(f"ERROR: filler unavailable: {exc}", file=sys.stderr)

    def choose(self, first_part):
        """A clip for render(filler=...), or None. It must end before the body is expected, or it
        would hold the body back; the estimate leans early so it rarely does."""
        settings = self.settings
        expected = (float(settings.get("arrival_base_ms", 650)) + float(
            settings.get("arrival_ms_per_char", 60)) * len(first_part)) / 1000
        self.chosen = pick_filler(self.pool, expected - self.after, self.last)
        # Not every late start gets a filler; people do not say えーと every time either.
        if random.random() >= float(settings.get("filler_probability", 1.0)):
            self.chosen = None
        return self.chosen and self.chosen[1]

    def heard(self, report):
        """After a line: if its filler played, avoid that take next and redraw pitch and speed."""
        if "filler" in report and self.chosen:
            self.last = self.chosen[0]
            self.prepare()
