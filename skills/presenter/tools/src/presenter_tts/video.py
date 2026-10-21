"""Export a narrated deck as MP4: one headless-browser screenshot per slide, held for that slide's
narration, muxed with narration.wav by FFmpeg."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from urllib.parse import quote, unquote

from .audio import AudioError
from .narrate import read_json, seconds, write_player

BROWSERS = ["chrome", "msedge", "google-chrome", "chromium",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"]


def browser():
    for name in [os.environ.get("PRESENTER_BROWSER"), *BROWSERS]:
        if name and (found := shutil.which(name) or (Path(name).is_file() and name)):
            return found
    raise AudioError("MP4 export needs Chrome or Edge; set PRESENTER_BROWSER to its path")


def screenshot(exe, page, png, profile, width, height):
    # html-deck slides are 1600x900; the device scale factor renders them at the video size.
    subprocess.run([exe, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
                    f"--user-data-dir={profile}", "--window-size=1600,900",
                    f"--force-device-scale-factor={width / 1600}", "--virtual-time-budget=5000",
                    f"--screenshot={png}", page.as_uri()],
                   check=False, capture_output=True, timeout=120)
    if not png.is_file():
        raise AudioError(f"The browser could not render {page}")


def export(narration, output=None, width=1920, height=1080, fps=30, log=print):
    """Write <narration>/presentation.mp4 (H.264/AAC) and link it from player.html."""
    narration = Path(narration)
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise AudioError(f"MP4 export needs {tool} (FFmpeg) on PATH")
    timeline = read_json(narration / "timeline.json")
    wav = narration / "narration.wav"
    total = seconds(wav)
    output = Path(output) if output else narration / "presentation.mp4"
    exe = browser()
    with tempfile.TemporaryDirectory(prefix=".presenter-video-", dir=narration) as work:
        work = Path(work).resolve()  # Chrome treats a relative --user-data-dir as unset and hands off to a running Chrome.
        lines = ["ffconcat version 1.0"]
        for index, slide in enumerate(timeline["slides"], 1):
            log(f"[{index}/{len(timeline['slides'])}] {slide['id']}: rendering")
            png = work / f"{index:04d}.png"
            screenshot(exe, (narration / unquote(slide["slide"])).resolve(), png, work / "profile", width, height)
            lines += [f"file '{png.name}'", f"duration {slide['end'] - slide['start']:.3f}"]
        lines.append(f"file '{png.name}'")  # The concat demuxer drops the last duration otherwise.
        (work / "frames.ffconcat").write_text("\n".join(lines) + "\n", encoding="utf-8")
        partial = output.with_name(f".{output.stem}.partial.mp4")
        log("encoding MP4")
        done = subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "1", "-i", str(work / "frames.ffconcat"),
             "-i", str(wav), "-map", "0:v:0", "-map", "1:a:0",
             "-vf", f"fps={fps},scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1",
             "-c:v", "libx264", "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", "-t", f"{total:.3f}", str(partial)],
            capture_output=True, text=True, errors="replace")
        if done.returncode:
            partial.unlink(missing_ok=True)
            raise AudioError(f"ffmpeg failed: {done.stderr.strip()[-500:]}")
    probe = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json",
                                       str(partial)], capture_output=True, text=True, check=True).stdout)
    kinds = {s["codec_type"] for s in probe["streams"]}
    length = float(probe["format"]["duration"])
    if kinds != {"video", "audio"} or abs(length - total) > 0.1:
        partial.unlink(missing_ok=True)
        raise AudioError(f"The MP4 came out wrong (streams {sorted(kinds)}, {length:.2f}s for {total:.2f}s of audio)")
    os.replace(partial, output)
    try:
        timeline["video"] = quote(Path(os.path.relpath(output.resolve(), narration.resolve())).as_posix(), safe="/")
    except ValueError:  # Another drive on Windows.
        timeline["video"] = output.resolve().as_uri()
    (narration / "timeline.json").write_text(json.dumps(timeline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_player(narration)
    return {"video": str(output), "seconds": round(length, 3), "width": width, "height": height, "fps": fps}
