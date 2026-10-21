"""Command line: presenter-tts doctor | plan | speak | say | voice add/list/remove."""

import functools
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib
from typing import Annotated, Optional
import webbrowser

import typer

from . import api as speech_api, server, voices
from .audio import AudioError, Backend, render
from .narrate import narrate as narrate_deck, read_json, voice_name as narrate_voice
from .play import server as play_server
from .say import say as say_loop
from .video import export as export_video
from .ssml import MAX_BYTES, SSMLError, compile_ssml, event_dict

app = typer.Typer(help="Local SSML 1.1 presentation speech with quantized Fish S2 Pro.",
                  no_args_is_help=True, pretty_exceptions_enable=False)
voice_app = typer.Typer(help="Clone, list and remove voice profiles (voices/).",
                        no_args_is_help=True)
app.add_typer(voice_app, name="voice")
server_app = typer.Typer(help="Run the s2.cpp TTS server in the background.", no_args_is_help=True)
app.add_typer(server_app, name="server")

VoiceOption = Annotated[Optional[str], typer.Option(
    help="Voice name from `voice list`, a .s2voice path, or default.")]


def handled(command):
    """Report expected failures as one ERROR line and exit 2, instead of a traceback."""
    @functools.wraps(command)
    def wrapper(*args, **kwargs):
        try:
            return command(*args, **kwargs)
        except (SSMLError, AudioError, OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            typer.echo(f"ERROR: {exc}", err=True)
            raise typer.Exit(2)
        except KeyboardInterrupt:
            typer.echo("Cancelled", err=True)
            raise typer.Exit(130)
    return wrapper


def load_config(ctx, voice=None):
    path = ctx.obj or voices.CONFIG
    if path.exists():
        config = tomllib.loads(path.read_text(encoding="utf-8-sig"))  # Tolerate an editor's BOM.
    elif ctx.obj is None:
        config = {"backend": "s2cpp"}  # No config.toml yet: defaults.
    else:
        raise AudioError(f"Config does not exist: {path}")
    # `voice` may be a name from voices/ (voice = "myvoice") or a .s2voice path.
    if voice or config.get("voice"):
        voices.set_voice(config, voice or config["voice"])
    return config


def ensure_server(config):
    if not server.reachable(config) and config.get("server", {}).get("auto_start", True):
        typer.echo("Starting the TTS server in the background (stop it with: server stop)...", err=True)
    server.ensure(config)


def read_document(source):
    if source == "-":
        return sys.stdin.buffer.read(MAX_BYTES + 1).decode("utf-8-sig")
    with Path(source).open("rb") as stream:
        return stream.read(MAX_BYTES + 1).decode("utf-8-sig")


@app.callback()
def main(ctx: typer.Context,
         config: Annotated[Optional[Path], typer.Option(
             help="TOML settings file (default: <home>/config.toml).")] = None):
    ctx.obj = config


@app.command()
@handled
def doctor(ctx: typer.Context):
    """Check GPU, toolchain, engine files, voices, network and the local TTS port (JSON).

    presenter-setup reads `engine.ready` and `prerequisites` to decide what still needs doing."""
    from urllib.parse import urlsplit
    import socket
    import re
    config = load_config(ctx)
    result = {"python": sys.version.split()[0], "ffmpeg": shutil.which("ffmpeg"),
              "ffplay": shutil.which("ffplay"), "backend": config.get("backend", "s2cpp")}
    try:
        engine_ok, engine_note = bool(voices.engine()), None
    except AudioError as exc:
        engine_ok, engine_note = False, str(exc)
    cuda_file = voices.RUNTIME / "s2-cuda-root.txt"
    cuda_root = cuda_file.read_text(encoding="utf-8-sig").strip() if cuda_file.is_file() else None
    result["home"] = {"path": str(voices.HOME), "config": voices.CONFIG.is_file(),
                      "voices": [p["name"] for p in voices.profiles()]}
    result["engine"] = {"ready": engine_ok, "missing": engine_note, "cuda_root": cuda_root,
                        "cache": str(voices.RUNTIME)}
    nvcc = str(Path(cuda_root) / "bin" / "nvcc.exe") if cuda_root else shutil.which("nvcc")
    cuda_version = None
    if nvcc and Path(nvcc).is_file():
        found = re.search(r"release (\d+\.\d+)", subprocess.run(
            [nvcc, "--version"], capture_output=True, text=True, timeout=30).stdout)
        cuda_version = found and found[1]
    vswhere = Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe")
    visual_studio = None
    if vswhere.is_file():
        visual_studio = subprocess.run(
            [str(vswhere), "-latest", "-products", "*", "-requires",
             "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
            capture_output=True, text=True, timeout=30).stdout.strip() or None
    result["prerequisites"] = {"windows": sys.platform == "win32", "uv": shutil.which("uv"),
                               "git": shutil.which("git"), "nvcc": nvcc, "cuda_version": cuda_version,
                               "visual_studio_cpp": visual_studio,
                               "cmake": shutil.which("cmake") or (visual_studio and "bundled with Visual Studio")}
    command = shutil.which("nvidia-smi")
    if command:
        proc = subprocess.run([command, "--query-gpu=name,memory.total,memory.free",
                               "--format=csv,noheader,nounits"], capture_output=True,
                              text=True, timeout=10)
        result["gpu"] = proc.stdout.strip() if not proc.returncode else proc.stderr.strip()
    for host in ("huggingface.co", "github.com"):
        try:
            socket.getaddrinfo(host, 443)
            result[host] = "DNS available"
        except OSError as exc:
            result[host] = f"DNS unavailable: {exc}"
    endpoint = urlsplit(Backend(config).url)
    try:
        with socket.create_connection((endpoint.hostname, endpoint.port or 80), timeout=1):
            result["tts_port"] = "reachable (TCP only; synthesis not verified)"
    except OSError as exc:
        result["tts_port"] = f"unavailable: {exc}"
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command()
@handled
def plan(source: Annotated[str, typer.Argument(metavar="INPUT", help="SSML file, or - for stdin.")]):
    """Show the speech and timing events an SSML document compiles to."""
    events, warnings = compile_ssml(read_document(source))
    typer.echo(json.dumps({"profile": "presenter-ssml-1.1-subset", "warnings": warnings,
                           "events": [event_dict(e) for e in events]}, ensure_ascii=False, indent=2))


@app.command()
@handled
def speak(ctx: typer.Context,
          source: Annotated[str, typer.Argument(metavar="INPUT", help="SSML file, or - for stdin.")],
          output: Annotated[Path, typer.Option(help="WAV to write.")] = Path("outputs/presentation.wav"),
          play: Annotated[bool, typer.Option(help="Play PCM as it arrives.")] = False,
          force: Annotated[bool, typer.Option(help="Overwrite an existing output.")] = False,
          voice: VoiceOption = None):
    """Render an SSML presentation to WAV."""
    config = load_config(ctx, voice)
    events, warnings = compile_ssml(read_document(source))
    for warning in warnings:
        typer.echo("WARNING: " + warning, err=True)
    ensure_server(config)
    report = render(events, Backend(config), output, play=play, force=force)
    typer.echo(json.dumps({"output": str(output), "timing": str(output.with_suffix(".json")),
                           "first_speech_pcm_seconds": report["first_speech_pcm_seconds"],
                           "rtf_speech": report["rtf_speech"]}, ensure_ascii=False, indent=2))


@app.command()
@handled
def narrate(ctx: typer.Context,
            deck: Annotated[Path, typer.Argument(exists=True, file_okay=False,
                                                 help="html-deck folder with presentation-map.json.")],
            narration: Annotated[Optional[Path], typer.Option(
                help="presentation-ja's folder (script.json, checked/). Default: <deck>/narration.")]
            = None,
            voice: VoiceOption = None,
            force: Annotated[bool, typer.Option(help="Resynthesize every slide, not just changed ones.")]
            = False,
            checked: Annotated[Optional[Path], typer.Option(
                help="presentation-ja's checked output folder containing receipt.json. "
                     "Default: <narration>/checked.")] = None):
    """Turn presentation-ja's checked per-slide script into narration audio and a player.

    Writes <narration>/audio/<slide>.wav, narration.wav, timeline.json and player.html."""
    config = load_config(ctx, voice)
    ensure_server(config)
    result = narrate_deck(config, deck, narration, force, log=lambda line: typer.echo(line, err=True),
                         checked=checked)
    folder = narration or deck / "narration"
    typer.echo(json.dumps({"player": str(folder / "player.html"), "audio": str(folder / "narration.wav"),
                           "timeline": str(folder / "timeline.json"), "slides": len(result["slides"]),
                           "total_seconds": result["total_seconds"]}, ensure_ascii=False, indent=2))


@app.command()
@handled
def video(deck: Annotated[Path, typer.Argument(exists=True, file_okay=False, help="html-deck folder.")],
          narration: Annotated[Optional[Path], typer.Option(
              help="Folder narrate wrote to. Default: <deck>/narration.")] = None,
          output: Annotated[Optional[Path], typer.Option(
              help="MP4 path. Default: <narration>/presentation.mp4.")] = None,
          width: Annotated[int, typer.Option(min=320, help="Video width; height follows 16:9.")] = 1920,
          fps: Annotated[int, typer.Option(min=1, max=60)] = 30):
    """Export the narrated deck as an MP4 (H.264/AAC) after `narrate`, and link it from player.html.

    Needs Chrome or Edge (PRESENTER_BROWSER overrides) and FFmpeg."""
    width -= width % 2  # yuv420p needs even dimensions.
    result = export_video(narration or deck / "narration", output, width, width * 9 // 16 // 2 * 2, fps,
                          log=lambda line: typer.echo(line, err=True))
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command()
@handled
def play(ctx: typer.Context,
         deck: Annotated[Path, typer.Argument(exists=True, file_okay=False, help="html-deck folder.")],
         narration: Annotated[Optional[Path], typer.Option(
             help="presentation-ja's folder (script.json, checked/). Default: <deck>/narration.")] = None,
         checked: Annotated[Optional[Path], typer.Option(
             help="Checked output folder containing receipt.json. Default: <narration>/checked.")] = None,
         port: Annotated[int, typer.Option(help="Port on 127.0.0.1; 0 picks a free one.")] = 0,
         open_browser: Annotated[bool, typer.Option("--open/--no-open", help="Open the player.")] = True):
    """Serve the player with a voice picker until Ctrl+C. A voice without audio is synthesized
    when picked (into <narration>/voices/<name>/); with no narration yet, the configured voice
    is synthesized first."""
    folder = narration or deck / "narration"

    def synthesize(name, log=lambda line: None):
        config = load_config(ctx, name)
        ensure_server(config)
        main = folder / "timeline.json"
        current = read_json(main).get("voice", "default") if main.is_file() else None
        chosen = narrate_voice(config)
        out = None if current in (None, chosen) else folder / "voices" / chosen

        def report(line):
            typer.echo(f"{chosen}: {line}", err=True)
            log(line)
        narrate_deck(config, deck, narration, log=report, checked=checked, out=out)

    if not (folder / "timeline.json").is_file():
        typer.echo("No narration yet; synthesizing the configured voice first.", err=True)
        synthesize(None)
    httpd, url = play_server(deck, folder, synthesize, port)
    typer.echo(json.dumps({"player": url}, ensure_ascii=False), err=False)
    sys.stdout.flush()
    if open_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


@app.command()
@handled
def say(ctx: typer.Context,
        text: Annotated[Optional[list[str]], typer.Argument(
            help="Text to speak once; omit to type lines interactively (/voice NAME switches).")] = None,
        output: Annotated[Path, typer.Option(help="WAV of the last line.")] = Path("outputs/say.wav"),
        voice: VoiceOption = None):
    """Speak typed text while it is generated."""
    config = load_config(ctx, voice)
    ensure_server(config)
    say_loop(Backend(config), " ".join(text or []), output)


@app.command()
@handled
def api(ctx: typer.Context,
        voice: Annotated[Optional[str], typer.Option(
            help="Voice when a request names none or an OpenAI voice (alloy, nova, ...).")] = None,
        host: Annotated[str, typer.Option(
            help="Address to listen on; 0.0.0.0 accepts other devices (API key required).")]
        = "0.0.0.0",
        port: Annotated[int, typer.Option(help="Port to listen on.")] = 8000,
        api_key: Annotated[Optional[str], typer.Option(
            envvar="PRESENTER_TTS_API_KEY",
            help="Bearer key clients must send; default: generated once into <home>/api-key.txt.")]
        = None):
    """Serve an OpenAI-compatible speech API: POST /v1/audio/speech.

    Point an OpenAI client at http://<this PC>:8000/v1 with the printed API key. A request's
    "voice" picks a registered voice by name. With --host 127.0.0.1 no key is needed."""
    config = load_config(ctx, voice)
    ensure_server(config)
    speech_api.serve(config, host, port, api_key)


@server_app.command("start")
@handled
def server_start(ctx: typer.Context):
    """Start s2.cpp in the background and wait until it accepts requests.

    GPU layers and codec placement come from [server] in the config (36 layers, codec on GPU)."""
    config = load_config(ctx)
    typer.echo("Starting the TTS server (loading the model)...")
    if server.start(config):
        typer.echo(f"Running: {server.status(config)['url']} (log: {server.LOG_FILE})")
    else:
        typer.echo("Already running")


@server_app.command("status")
@handled
def server_status(ctx: typer.Context):
    """Show whether the TTS server is listening and which process this tool started."""
    info = server.status(load_config(ctx))
    state = "listening" if info["listening"] else "not running"
    owner = f", pid {info['pid']} (started here)" if info["pid"] else (
        ", started elsewhere" if info["listening"] else "")
    typer.echo(f"{info['url']}: {state}{owner}")
    typer.echo(f"log: {info['log']}")


@server_app.command("stop")
@handled
def server_stop():
    """Stop the TTS server started by `server start` (frees about 4.6 GB of VRAM)."""
    typer.echo("Stopped" if server.stop() else "No server started by this tool is running")


@voice_app.command("add")
@handled
def voice_add(
        name: Annotated[str, typer.Argument(help="New voice name (letters, digits, - and _).")],
        audio: Annotated[Path, typer.Argument(exists=True, dir_okay=False,
                                              help="Reference recording (wav, mp3, m4a, ...).")],
        text: Annotated[Optional[str], typer.Option("--text", "-t",
                                                    help="Exactly what the recording says.")] = None,
        text_file: Annotated[Optional[Path], typer.Option(exists=True, dir_okay=False,
                                                          help="UTF-8 file holding the transcript.")] = None,
        sample: Annotated[str, typer.Option(help="Sentence spoken in the new voice as a check.")]
        = "これは、新しく登録した声のテストです。",
        boost: Annotated[bool, typer.Option(help="Raise the reference to a -3 dB peak first.")] = True,
        gpu: Annotated[bool, typer.Option(help="Encode on the GPU (stop the TTS server first).")] = False,
        preview: Annotated[bool, typer.Option(help="Play the check sentence when done.")] = True,
        force: Annotated[bool, typer.Option(help="Replace an existing voice of this name.")] = False):
    """Clone a voice from a recording and its transcript.

    Only clone voices you have permission to use."""
    if (text is None) == (text_file is None):
        raise AudioError("Give the transcript with exactly one of --text or --text-file")
    transcript = text if text is not None else text_file.read_text(encoding="utf-8-sig").strip()
    sample_output = Path("outputs") / f"voice-{name}.wav"
    typer.echo(f"Encoding '{name}' on the {'GPU' if gpu else 'CPU'}; this takes about a minute...")
    meta = voices.clone(name, audio, transcript, sample_text=sample, sample_output=sample_output,
                        force=force, gpu=gpu, boost=boost)
    for warning in meta["warnings"]:
        typer.echo("WARNING: " + warning, err=True)
    typer.echo(f"Saved voice '{name}': {meta['seconds']} s reference, gain {meta['gain_db']:+} dB")
    typer.echo(f"Check sentence: {sample_output}")
    typer.echo(f"Use it with: python -m presenter_tts say --voice {name}")
    if preview and shutil.which("ffplay"):
        subprocess.run([shutil.which("ffplay"), "-loglevel", "error", "-nodisp", "-autoexit",
                        str(sample_output)])


@voice_app.command("list")
@handled
def voice_list(ctx: typer.Context):
    """Show registered voices; * marks the one config.toml uses."""
    current = Path(load_config(ctx).get("voice", "default")).stem
    typer.echo(f"{'*' if current == 'default' else ' '} default  (model's own voice)")
    for profile in voices.profiles():
        mark = "*" if profile["name"] == current else " "
        details = f"{profile['seconds']} s  {profile['transcript']}" if "transcript" in profile else ""
        typer.echo(f"{mark} {profile['name']}  {details}".rstrip())


@voice_app.command("remove")
@handled
def voice_remove(name: Annotated[str, typer.Argument(help="Voice to delete.")],
                 yes: Annotated[bool, typer.Option("--yes", "-y", help="Do not ask.")] = False):
    """Delete a voice profile, its reference copy and its cached fillers."""
    if not yes:
        typer.confirm(f"Delete voice '{name}'?", abort=True)
    voices.remove(name)
    typer.echo(f"Removed voice '{name}'")
