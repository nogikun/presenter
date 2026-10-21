"""Voice profiles: clone a reference recording into voices/<name>.s2voice with s2.cpp."""

from array import array
from datetime import datetime
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import wave

from .audio import AudioError

PROJECT = Path(__file__).resolve().parents[2]  # src/presenter_tts/voices.py -> repository root
# Like uv: .runtime holds what can be rebuilt (engine, model, caches) and may be deleted any time;
# voices/ holds profiles and recordings that cannot be, so it lives apart (both gitignored).
RUNTIME = PROJECT / ".runtime"
VOICES = PROJECT / "voices"
FILLER_CACHE = RUNTIME / "fillers"
NAME = re.compile(r"[A-Za-z0-9_-]+")
# s2.cpp recommends a 5-30 s reference; shorter clips clone poorly.
RECOMMENDED_SECONDS = (5, 30)


def set_voice(config, name):
    """Select voices/<name>.s2voice (or a .s2voice path); "default" drops the clone."""
    if name == "default":
        config.pop("voice", None)
        return
    path = Path(name) if name.endswith(".s2voice") else VOICES / f"{name}.s2voice"
    if not path.is_file():
        names = ", ".join(["default"] + [p["name"] for p in profiles()])
        raise AudioError(f"Voice not found: {name} (available: {names})")
    config["voice"] = path.resolve().as_posix()


def profiles():
    """Registered voices as dicts: name, path and whatever `voice add` recorded about them."""
    result = []
    for path in sorted(VOICES.glob("*.s2voice")):
        meta = path.with_suffix(".json")
        info = json.loads(meta.read_text(encoding="utf-8")) if meta.is_file() else {}
        result.append({"name": path.stem, "path": path, **info})
    return result


def normalize(path, peak_db=-3.0, max_gain_db=20.0):
    """Scale a PCM16 WAV so its peak sits at peak_db; a quiet reference clones a quiet voice."""
    with wave.open(str(path), "rb") as clip:
        params = clip.getparams()
        samples = array("h", clip.readframes(clip.getnframes()))
    peak = max(map(abs, samples), default=0)
    if not peak:
        raise AudioError(f"Reference audio is silent: {path}")
    gain = min(10 ** (peak_db / 20) * 32767 / peak, 10 ** (max_gain_db / 20))
    scaled = array("h", (max(-32768, min(32767, round(s * gain))) for s in samples))
    with wave.open(str(path), "wb") as clip:
        clip.setparams(params)
        clip.writeframes(scaled.tobytes())
    return 20 * math.log10(gain)


def engine():
    """s2.cpp executable, model, tokenizer and an environment that finds its CUDA DLLs."""
    executable = RUNTIME / "s2-build" / "Release" / "s2.exe"
    model = RUNTIME / "models" / "s2-pro-q4_k_m.gguf"
    for path in (executable, model, model.with_name("tokenizer.json")):
        if not path.is_file():
            raise AudioError(f"Missing {path}. Run tools/setup-s2.ps1 first.")
    env = dict(os.environ)
    cuda = RUNTIME / "s2-cuda-root.txt"
    if cuda.is_file():
        # utf-8-sig: Windows PowerShell 5.1 writes a BOM, which strip() would leave in the path.
        root = Path(cuda.read_text(encoding="utf-8-sig").strip())
        env["PATH"] = os.pathsep.join([str(root / "bin"), str(root / "bin" / "x64"), env["PATH"]])
    return executable, model, model.with_name("tokenizer.json"), env


def clone(name, audio, transcript, *, sample_text, sample_output, force=False, gpu=False,
          boost=True):
    """Encode `audio` (any format FFmpeg reads) plus its exact transcript into a voice profile.

    Runs on the CPU by default so it does not compete with a TTS server holding the GPU.
    Returns the metadata saved beside the profile, including warnings for the caller to show."""
    if not NAME.fullmatch(name) or name == "default":
        raise AudioError("Voice names use letters, digits, - and _ only, and cannot be 'default'")
    if not transcript.strip():
        raise AudioError("The transcript is required: write exactly what the recording says")
    profile = VOICES / f"{name}.s2voice"
    if profile.exists() and not force:
        raise AudioError(f"Voice already exists: {name} (use --force to replace it)")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AudioError("FFmpeg is required to read the reference audio")
    executable, model, tokenizer, env = engine()
    VOICES.mkdir(parents=True, exist_ok=True)
    # Build everything in a staging folder and swap it in only after s2.cpp succeeded, so a failed
    # `--force` re-clone (OOM, bad audio) leaves the existing voice untouched.
    staging = Path(tempfile.mkdtemp(prefix=f".{name}-", dir=VOICES))
    try:
        reference = staging / f"{name}.wav"
        proc = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(audio), "-ac", "1",
                               "-ar", "44100", "-sample_fmt", "s16", str(reference)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        if proc.returncode:
            raise AudioError(f"FFmpeg could not read {audio}: {proc.stderr.strip()[-300:]}")
        gain_db = normalize(reference) if boost else 0.0
        with wave.open(str(reference), "rb") as clip:
            seconds = clip.getnframes() / clip.getframerate()
        command = [str(executable), "--model", str(model), "--tokenizer", str(tokenizer),
                   "--prompt-audio", str(reference), "--prompt-text", transcript, "--voice", name,
                   "--save-voice", "--voice-dir", str(staging), "--text", sample_text,
                   "--output", str(sample_output)]
        if gpu:
            command += ["--cuda", "0", "--gpu-layers", "36", "--codec-follow-backend"]
        Path(sample_output).parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env)
        if proc.returncode or not (staging / profile.name).is_file():
            tail = "\n".join((proc.stdout + proc.stderr).strip().splitlines()[-5:])
            raise AudioError(f"s2.cpp could not save the voice:\n{tail}")
        meta = {"transcript": transcript, "source": str(audio), "seconds": round(seconds, 2),
                "gain_db": round(gain_db, 1),
                "created": datetime.now().isoformat(timespec="seconds")}
        (staging / f"{name}.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n",
                                              encoding="utf-8")
        for suffix in (".wav", ".json", ".s2voice"):  # The profile last: it marks the voice usable.
            os.replace(staging / f"{name}{suffix}", VOICES / f"{name}{suffix}")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    # Fillers are synthesized per voice; a replaced voice must not reuse the old ones.
    shutil.rmtree(FILLER_CACHE / name, ignore_errors=True)
    low, high = RECOMMENDED_SECONDS
    warnings = []
    if not low <= seconds <= high:
        warnings.append(f"Reference is {seconds:.1f} s; {low}-{high} s clones more reliably")
    return {**meta, "warnings": warnings}


def remove(name):
    """Delete a voice profile, its reference copy, metadata and cached fillers."""
    profile = VOICES / f"{name}.s2voice"
    if not NAME.fullmatch(name) or not profile.is_file():
        raise AudioError(f"Voice not found: {name}")
    for path in (profile, profile.with_suffix(".wav"), profile.with_suffix(".json")):
        path.unlink(missing_ok=True)
    shutil.rmtree(FILLER_CACHE / name, ignore_errors=True)
