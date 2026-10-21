"""Narrate an html-deck deck from presentation-ja's checked script: per-slide WAVs, one joined WAV,
a timeline and an HTML player that shows each slide while its narration plays."""

import hashlib
import html
from importlib import resources
import json
import os
from pathlib import Path
import re
from urllib.parse import quote
import wave

from .audio import AudioError, Backend, render
from .say import segments as split
from .ssml import Pause, Speech, Style


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise AudioError(f"Missing {path}")
    except json.JSONDecodeError as exc:
        raise AudioError(f"Invalid JSON in {path}: {exc}")


def plan(deck, narration, checked=None):
    """[(slide, segments)] in deck order, refusing a script that no longer matches what
    presentation-ja checked or the slides it was written for."""
    map_path, script_path = deck / "presentation-map.json", narration / "script.json"
    checked = Path(checked) if checked is not None else narration / "checked"
    receipt = read_json(checked / "receipt.json")
    deck_map, script = read_json(map_path), read_json(script_path)
    stale = "changed after presentation-ja's check; run its check and verify again"
    if receipt.get("script_sha256") != sha(script_path.read_bytes()):
        raise AudioError(f"{script_path} {stale}")
    if receipt.get("deck_map_sha256") != sha(map_path.read_bytes()):
        raise AudioError(f"{map_path} {stale}")
    # The map also hashes deck.md, theme.css, index.html (slide order) and every referenced asset;
    # a change to any of them means the narration was checked against a different deck.
    for name, digest in deck_map.get("dependencies", {}).items():
        path = deck / name
        if not path.is_file() or sha(path.read_bytes()) != digest:
            raise AudioError(f"{path} changed since the narration was written; "
                             "re-sync the map and review the script with presentation-ja")
    listed = {s["file"] for s in deck_map.get("slides", [])}
    extra = sorted(p.relative_to(deck).as_posix() for p in (deck / "slides").glob("*.html")
                   if p.relative_to(deck).as_posix() not in listed)
    if extra:
        raise AudioError(f"Slides not in the narration map: {', '.join(extra)}; {stale}")
    slides = []
    for slide in deck_map.get("slides", []):
        html = deck / slide["file"]
        if not html.is_file() or sha(html.read_bytes()) != slide["html_sha256"]:
            raise AudioError(f"{html} changed since the narration was written; "
                             "re-sync the map and review that slide's script with presentation-ja")
        parts = [s for s in script.get("segments", []) if s.get("slide_id") == slide["id"]]
        if not parts or any(s.get("slide_sha256") != slide["html_sha256"] for s in parts):
            raise AudioError(f"The script for slide {slide['id']} does not match it; {stale}")
        slides.append((slide, parts))
    if not slides:
        raise AudioError(f"{map_path} lists no slides")
    return slides


def events(parts, max_chars):
    """Speech per sentence/clause (long requests are slower), then each segment's explicit pause."""
    result = []
    for part in parts:
        result += [Speech(text, Style()) for text in split(part["text"], False, max_chars)]
        if part.get("pause_after_seconds"):
            result.append(Pause(float(part["pause_after_seconds"])))
    return result


def seconds(path):
    with wave.open(str(path), "rb") as clip:
        return clip.getnframes() / clip.getframerate()


def join(paths, output):
    with wave.open(str(paths[0]), "rb") as first:
        params = first.getparams()
    with wave.open(str(output), "wb") as out:
        out.setparams(params)
        for path in paths:
            with wave.open(str(path), "rb") as clip:
                if clip.getparams()[:3] != params[:3]:
                    raise AudioError(f"{path} has a different audio format from the other slides")
                out.writeframes(clip.readframes(clip.getnframes()))


def deck_title(deck):
    """html-deck keeps the deck name in index.html's <title>; fall back to the folder name."""
    index = deck / "index.html"
    found = re.search(r"<title>([^<]+)</title>", index.read_text(encoding="utf-8")) if index.is_file() else None
    return html.unescape(found.group(1)).strip() if found else deck.resolve().name


def player(timeline):
    template = resources.files("presenter_tts").joinpath("player.html").read_text(encoding="utf-8")
    data = json.dumps(timeline, ensure_ascii=False).replace("</", "<\\/")
    return template.replace("/*TIMELINE*/null", data)


def voice_name(config):
    return Path(config["voice"]).stem if config.get("voice") else "default"


def voice_outputs(narration):
    """Every voice that has audio: the main narration first, then each voices/<name>/ folder.
    Timeline paths are relative to the narration folder, where player.html lives."""
    narration = Path(narration)
    found = []
    if (narration / "timeline.json").is_file():
        main = read_json(narration / "timeline.json")
        found.append({"name": main.get("voice", "default"), "timeline": "timeline.json",
                      **({"video": main["video"]} if main.get("video") else {})})
    for path in sorted((narration / "voices").glob("*/timeline.json")):
        if all(v["name"] != path.parent.name for v in found):
            found.append({"name": path.parent.name, "timeline": f"voices/{path.parent.name}/timeline.json"})
    return found


def write_player(narration):
    """player.html opens with the main narration and carries the other voices' timelines, so even
    a player opened as a file (which cannot fetch them) can switch voices."""
    narration = Path(narration)
    main = read_json(narration / "timeline.json")
    others = [{**v, "data": read_json(narration / v["timeline"])} if v["timeline"] != "timeline.json" else v
              for v in voice_outputs(narration)]
    (narration / "player.html").write_text(player({**main, "voices": others}), encoding="utf-8")


def narrate(config, deck, narration=None, force=False, log=print, checked=None, out=None):
    """Synthesize what changed, then rewrite narration.wav, timeline.json and player.html.

    out: where the audio goes (default: the narration folder). The player switches voices between
    the main narration and <narration>/voices/<name>/, so each voice keeps its own audio there."""
    deck = Path(deck)
    narration = Path(narration) if narration else deck / "narration"
    out = Path(out) if out else narration
    slides = plan(deck, narration, checked)
    try:
        slide_urls = [quote(Path(os.path.relpath((deck / slide["file"]).resolve(),
                                               out.resolve())).as_posix(), safe="/")
                      for slide, _ in slides]
    except ValueError as exc:
        raise AudioError("Deck and narration must be on the same drive for relative slide URLs") from exc
    audio = out / "audio"
    audio.mkdir(parents=True, exist_ok=True)
    cache_path = audio / ".cache.json"
    cache = read_json(cache_path) if cache_path.is_file() else {}
    voice = config.get("voice")
    # Anything that changes the sound invalidates a slide's audio; unchanged slides are reused.
    basis = {"voice": sha(Path(voice).read_bytes()) if voice else "default",
             "backend": config.get("backend", "s2cpp"),
             "speaker": config.get("speaker", 3), "url": config.get("url"),
             "generation": config.get("generation", {}),
             "max_chars": int(config.get("say", {}).get("max_chars", 40))}
    backend, timeline, start = Backend(config), [], 0.0
    for index, ((slide, parts), slide_url) in enumerate(zip(slides, slide_urls), 1):
        key = sha(json.dumps({**basis, "segments": [[p["text"], p.get("pause_after_seconds", 0)]
                                                    for p in parts]},
                             ensure_ascii=False, sort_keys=True).encode())
        wav = audio / f"{slide['id']}.wav"
        if force or cache.get(slide["id"]) != key or not wav.is_file() or not wav.with_suffix(".json").is_file():
            log(f"[{index}/{len(slides)}] {slide['id']}: synthesizing")
            render(events(parts, basis["max_chars"]), backend, wav, force=True)
            cache[slide["id"]] = key
            cache_path.write_text(json.dumps(cache, indent=2) + "\n", encoding="utf-8")
        else:
            log(f"[{index}/{len(slides)}] {slide['id']}: unchanged, reused")
        length = seconds(wav)
        # render() records where each sentence landed; the player's transcript seeks by these.
        report = read_json(wav.with_suffix(".json"))
        rate = report.get("sample_rate") or 44100
        cues = [{"text": c["text"], "start": round(start + c["start_frame"] / rate, 3),
                 "end": round(start + c["end_frame"] / rate, 3)} for c in report.get("segments", [])]
        timeline.append({"position": slide["position"], "id": slide["id"],
                         "title": slide.get("claim") or slide.get("title") or slide["id"],
                         "slide": slide_url,
                         "audio": f"audio/{wav.name}", "cues": cues,
                         "start": round(start, 3), "end": round(start + length, 3)})
        start += length
    join([audio / f"{s['id']}.wav" for s, _ in slides], out / "narration.wav")
    result = {"title": deck_title(deck), "voice": voice_name(config), "total_seconds": round(start, 3),
              "slides": timeline}
    (out / "timeline.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                       encoding="utf-8")
    if (narration / "timeline.json").is_file():
        write_player(narration)
    return result
