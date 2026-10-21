"""Dependency-free checks for presentation-ja; never a semantic quality score."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import sys
import unicodedata

# Script entry points are also imported directly by the offline test harness.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import html_deck_bridge as deck_bridge

VERSION = "0.2.0"
PACE = {"slow": 240, "fast": 360}  # Engineering defaults, not population norms.


class ContractError(ValueError):
    pass


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def loads(data: bytes):
    def invalid(value):
        raise ContractError(f"non-finite JSON constant: {value}")
    return json.loads(data.decode("utf-8-sig"), object_pairs_hook=_pairs,
                      parse_constant=invalid)


def dump(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def require(condition, message):
    if not condition:
        raise ContractError(message)


def text(value, location):
    require(isinstance(value, str) and bool(value.strip()), f"{location}: nonempty string required")
    return value


def number(value, location, positive=False):
    require(type(value) in (int, float) and math.isfinite(value), f"{location}: finite number required")
    require(value > 0 if positive else value >= 0, f"{location}: invalid range")
    return value


def strings(value, location):
    require(isinstance(value, list), f"{location}: array required")
    for item in value:
        text(item, location)


def validate(brief, script):
    require(isinstance(brief, dict) and isinstance(script, dict), "brief and script must be objects")
    for name, doc in (("brief", brief), ("script", script)):
        require(type(doc.get("schema_version")) is int and doc["schema_version"] == 1,
                f"{name}.schema_version: expected integer 1")
    for field in ("audience", "goal"):
        text(brief.get(field), f"brief.{field}")
    require(brief.get("prior_knowledge") in ("novice", "mixed", "expert"), "invalid prior_knowledge")
    require(brief.get("delivery") in ("live", "tts"), "invalid delivery")
    number(brief.get("duration_seconds"), "duration_seconds", positive=True)
    strings(brief.get("must_preserve", []), "must_preserve")
    pace = brief.get("pace_cpm", PACE)
    require(isinstance(pace, dict), "pace_cpm: object required")
    slow = number(pace.get("slow"), "pace_cpm.slow", positive=True)
    fast = number(pace.get("fast"), "pace_cpm.fast", positive=True)
    require(slow <= fast, "pace_cpm.slow must be <= fast")
    require(isinstance(brief.get("sources"), list), "sources: array required")
    sources = {}
    for source in brief["sources"]:
        require(isinstance(source, dict), "source: object required")
        sid = text(source.get("id"), "source.id")
        require(sid not in sources, f"duplicate source id: {sid}")
        require(source.get("origin") in ("user-provided", "public-source", "synthetic", "human-authored"),
                f"source {sid}: explicit origin required")
        sources[sid] = text(source.get("text"), f"source {sid}.text")
    for field in ("main_message", "framework", "rationale"):
        text(script.get(field), f"script.{field}")
    for field in ("assumptions", "questions"):
        strings(script.get(field), f"script.{field}")
    require(isinstance(script.get("glossary"), list), "glossary: array required")
    for entry in script["glossary"]:
        require(isinstance(entry, dict), "glossary entry: object required")
        for field in ("term", "reading", "explanation"):
            text(entry.get(field), f"glossary.{field}")
    segments = script.get("segments")
    require(isinstance(segments, list) and bool(segments), "segments: nonempty array required")
    ids = set()
    for segment in segments:
        require(isinstance(segment, dict), "segment: object required")
        sid = text(segment.get("id"), "segment.id")
        require(re.fullmatch(r"[A-Za-z0-9_-]+", sid) is not None, "segment.id: use ASCII letters, digits, _ or -")
        require(sid not in ids, f"duplicate segment id: {sid}")
        ids.add(sid)
        require(segment.get("role") in ("opening", "body", "closing"), f"{sid}: invalid role")
        body = text(segment.get("text"), f"{sid}.text")
        require(re.search(r"</?[A-Za-z][^>]*>|```|^\s*(?:#{1,6}\s|[-*]\s)", body, re.M) is None,
                f"{sid}: markup in spoken text")
        if "cue" in segment:
            require(isinstance(segment["cue"], str), f"{sid}.cue: string required")
        number(segment.get("pause_after_seconds"), f"{sid}.pause_after_seconds")
        evidence = segment.get("evidence")
        require(isinstance(evidence, list), f"{sid}.evidence: array required")
        for entry in evidence:
            require(isinstance(entry, dict), f"{sid}: evidence object required")
            source_id = entry.get("source_id")
            require(isinstance(source_id, str) and source_id in sources, f"{sid}: unknown source id")
            claim = text(entry.get("claim"), f"{sid}.claim")
            quote = text(entry.get("quote"), f"{sid}.quote")
            require(claim in body, f"{sid}: claim is not an exact spoken-text excerpt")
            require(quote in sources[source_id], f"{sid}: quote is not an exact source excerpt")
    return sources, pace


def numeric_tokens(value):
    value = unicodedata.normalize("NFKC", value)
    # Recognize a decimal as one token. Units/subjects still require semantic review.
    return set(re.findall(r"(?<![0-9A-Za-z_.])\d+(?:,\d{3})*(?:\.\d+)?(?:%|％)?", value))


def deck_alignment(brief, script, deck_map, map_sha256):
    bound = "deck_binding" in script or any("slide_id" in s or "slide_sha256" in s for s in script["segments"])
    if deck_map is None:
        require(not bound and "slide_targets" not in brief, "slide-bound script requires --deck and a deck map")
        return None
    deck_bridge.validate_manifest(deck_map)
    binding = script.get("deck_binding")
    require(isinstance(binding, dict) and binding.get("map_sha256") == map_sha256,
            "script deck binding is missing or stale; review changes before rebinding")
    slides = {s["id"]: s for s in deck_map["slides"]}
    covered = set()
    last_position = 0
    for segment in script["segments"]:
        sid = segment.get("slide_id")
        require(isinstance(sid, str) and sid in slides, f"{segment['id']}: unknown or missing slide_id")
        slide = slides[sid]
        require(segment.get("slide_sha256") == slide["html_sha256"], f"{segment['id']}: slide content changed")
        require(slide["position"] >= last_position, "script order differs from slide order")
        covered.add(sid)
        last_position = slide["position"]
    require(covered == set(slides), "each slide needs at least one spoken segment; a slide is missing")
    targets = brief.get("slide_targets", {})
    require(isinstance(targets, dict), "slide_targets must be an object")
    if targets:
        require(set(targets) == set(slides), "slide_targets must cover exactly the current slide IDs")
        for sid, value in targets.items():
            number(value, f"slide_targets.{sid}", positive=True)
        require(sum(targets.values()) <= brief["duration_seconds"], "slide targets exceed overall duration")
    return slides


def assess(brief, script, audio_seconds=None, deck_map=None, map_sha256=None):
    sources, pace = validate(brief, script)
    aligned_slides = deck_alignment(brief, script, deck_map, map_sha256)
    if audio_seconds is not None:
        number(audio_seconds, "audio_seconds", positive=True)
    findings = []
    spoken = "\n".join(s["text"] for s in script["segments"])
    source_numbers = numeric_tokens("\n".join(sources.values()))
    for segment in script["segments"]:
        sid, body = segment["id"], segment["text"]
        for value in sorted(numeric_tokens(body) - source_numbers):
            findings.append({"rule": "number_not_in_sources", "segment_id": sid,
                             "excerpt": value, "message": "資料にない数値の候補。言い換えや計算も含め意味を確認する。"})
        if not segment["evidence"]:
            findings.append({"rule": "no_evidence_mapping", "segment_id": sid,
                             "excerpt": body[:100], "message": "事実を述べる箇所がないか、根拠との対応を人が確認する。"})
        for sentence in re.split(r"[。！？!?]\s*", body):
            if len(sentence.strip()) > 80:
                findings.append({"rule": "long_sentence", "segment_id": sid,
                                 "excerpt": sentence.strip(), "message": "一度聞いて修飾と条件を追えるか確認する。"})
        if brief["prior_knowledge"] == "novice":
            for term in sorted(set(re.findall(r"\b[A-Z][A-Z0-9-]{1,}\b", body))):
                entries = [g for g in script["glossary"] if g["term"] == term]
                if not any(g["explanation"] in spoken for g in entries):
                    findings.append({"rule": "abbreviation_review", "segment_id": sid,
                                     "excerpt": term, "message": "略語の意味と読み方が初出で伝わるか確認する。"})
    count = sum(unicodedata.category(c)[0] in "LN" for c in spoken)
    pauses = sum(s["pause_after_seconds"] for s in script["segments"])
    lower = count * 60 / pace["fast"] + pauses
    upper = count * 60 / pace["slow"] + pauses
    target = brief["duration_seconds"]
    if audio_seconds is not None and audio_seconds > target:
        findings.append({"rule": "measured_over_budget", "message": "申告された全体音声の実測時間が持ち時間を超える。"})
    elif audio_seconds is None and upper > target:
        findings.append({"rule": "estimated_over_budget", "message": "概算の上限が持ち時間を超える。音読または生成音声で確認する。"})
    result = {
        "schema_version": 1, "checker_version": VERSION,
        "status": "needs_review" if findings else "mechanical_checks_completed",
        "hard_errors": [], "review_findings": findings,
        "timing": {"counted_letters_and_numbers": count, "explicit_pause_seconds": pauses,
                   "pace_cpm": pace, "estimated_seconds": [round(lower, 2), round(upper, 2)],
                   "target_seconds": target, "measured_audio_seconds": audio_seconds,
                   "method": "engineering-estimate; not a validated Japanese speech-rate model"},
        "semantic_fidelity": "not_assessed", "human_listening": "not_assessed",
        "notice": "No quality score. No findings does not establish factual correctness or intelligibility."
    }
    if aligned_slides is not None:
        slide_timings = []
        lower_start, upper_start = 0, 0
        for slide in deck_map["slides"]:
            segments = [s for s in script["segments"] if s["slide_id"] == slide["id"]]
            chars = sum(unicodedata.category(c)[0] in "LN" for s in segments for c in s["text"])
            pauses = sum(s["pause_after_seconds"] for s in segments)
            lo, hi = chars * 60 / pace["fast"] + pauses, chars * 60 / pace["slow"] + pauses
            target = brief.get("slide_targets", {}).get(slide["id"])
            slide_timings.append({"slide_id": slide["id"], "position": slide["position"],
                                  "file": slide["file"], "title": slide["title"], "claim": slide["claim"],
                                  "segment_ids": [s["id"] for s in segments], "target_seconds": target,
                                  "estimated_seconds": [round(lo, 2), round(hi, 2)],
                                  "estimated_start_seconds": [round(lower_start, 2), round(upper_start, 2)],
                                  "measured_audio_seconds": None})
            if target is not None and hi > target:
                findings.append({"rule": "slide_estimated_over_budget", "slide_id": slide["id"],
                                 "message": "このスライドの概算上限が配分した時間を超える。"})
            lower_start += lo
            upper_start += hi
        result["slides"] = slide_timings
        result["slide_alignment"] = "structural_only"
        result["status"] = "needs_review" if findings else "mechanical_checks_completed"
    return result


def render(script, deck_map=None, result=None):
    manuscript = "\n".join(s["text"] for s in script["segments"]) + "\n"
    if deck_map is not None:
        notes = ["# スライド別の発表原稿\n", "時間は文字速度による概算です。実測ではありません。\n"]
        outputs = {"manuscript.txt": manuscript, "slide-timing.json": dump(result["slides"])}
        for slide, timing in zip(deck_map["slides"], result["slides"]):
            segments = [s for s in script["segments"] if s["slide_id"] == slide["id"]]
            notes.extend([f"## スライド {slide['position']}: {slide['claim']}\n",
                          f"対応: `{slide['file']}` / ID: `{slide['id']}`\n",
                          f"概算: {timing['estimated_seconds'][0]}〜{timing['estimated_seconds'][1]}秒\n"])
            if timing["target_seconds"] is not None:
                notes.append(f"配分: {timing['target_seconds']}秒\n")
            for segment in segments:
                notes.append(segment["text"] + "\n")
                if segment.get("cue"):
                    notes.append("演出指示（読み上げない）: " + segment["cue"] + "\n")
                notes.append(f"本文後の間: {segment['pause_after_seconds']}秒\n")
            outputs[f"by-slide/{slide['id']}.txt"] = "\n".join(s["text"] for s in segments) + "\n"
        outputs["speaker-notes.md"] = "\n".join(notes)
        return outputs
    notes = ["# 発表原稿と演出指示\n"]
    for segment in script["segments"]:
        notes.extend([f"## {segment['id']}\n", segment["text"] + "\n"])
        if segment.get("cue"):
            notes.append("演出指示（読み上げない）: " + segment["cue"] + "\n")
        notes.append(f"本文後の間: {segment['pause_after_seconds']}秒\n")
    return {"manuscript.txt": manuscript, "speaker-notes.md": "\n".join(notes)}


def policy_hashes():
    root = Path(__file__).resolve().parents[1]
    files = [root / "SKILL.md", *sorted((root / "references").glob("*.md"))]
    return {p.relative_to(root).as_posix(): sha(p.read_bytes()) for p in files}


def check_files(brief_path: Path, script_path: Path, out: Path, audio_seconds=None, verify=False,
                deck_path: Path | None = None, deck_map_path: Path | None = None):
    require(deck_path is not None or deck_map_path is None, "--deck-map also requires --deck")
    deck_map, map_sha256 = None, None
    input_paths = {brief_path.resolve(), script_path.resolve()}
    if deck_path is not None:
        deck_map_path = deck_map_path or deck_path / "presentation-map.json"
        deck_map, map_sha256 = deck_bridge.load_current(deck_path, deck_map_path)
        input_paths.add(deck_map_path.resolve())
        input_paths.update((deck_path / name).resolve() for name in deck_map["dependencies"])
    brief_bytes, script_bytes = brief_path.read_bytes(), script_path.read_bytes()
    brief, script = loads(brief_bytes), loads(script_bytes)
    result = assess(brief, script, audio_seconds, deck_map, map_sha256)
    rendered = render(script, deck_map, result)
    rendered["check.json"] = dump(result)
    prior_receipt = out / "receipt.json"
    if not verify and prior_receipt.exists():
        previous = loads(prior_receipt.read_bytes())
        require(isinstance(previous, dict) and isinstance(previous.get("outputs"), dict), "invalid previous receipt")
        require(set(previous["outputs"]) == set(rendered),
                "output file set changed; use a new output directory to avoid leaving obsolete slide manuscripts")
    for name in set(rendered) | {"receipt.json"}:
        target = (out / name).resolve()
        require(target.is_relative_to(out.resolve()), "output path escapes output directory")
        require(target not in input_paths, "output would overwrite an input file")
    receipt = {
        "schema_version": 1,
        "brief_sha256": sha(brief_bytes), "script_sha256": sha(script_bytes),
        "checker_sha256": sha(Path(__file__).read_bytes()), "policy_sha256": policy_hashes(),
        "support_sha256": {p.name: sha(p.read_bytes()) for p in sorted(Path(__file__).parent.glob("*.py"))},
        "measured_audio_seconds": audio_seconds,
        "outputs": {name: sha(value.encode("utf-8")) for name, value in rendered.items()}
    }
    if deck_map is not None:
        receipt["deck_map_sha256"] = map_sha256
    if verify:
        saved = loads((out / "receipt.json").read_bytes())
        require(saved == receipt, "receipt mismatch: input, policy, checker, or measurement changed")
        for name, digest in receipt["outputs"].items():
            require(sha((out / name).read_bytes()) == digest, f"checked output changed: {name}")
        return {"verified": True, "script_sha256": receipt["script_sha256"],
                "semantic_fidelity": "not_assessed", "human_listening": "not_assessed"}
    out.mkdir(parents=True, exist_ok=True)
    for name, value in rendered.items():
        (out / name).parent.mkdir(parents=True, exist_ok=True)
        (out / name).write_bytes(value.encode("utf-8"))
    (out / "receipt.json").write_bytes(dump(receipt).encode("utf-8"))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "verify"))
    parser.add_argument("--brief", required=True, type=Path)
    parser.add_argument("--script", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--audio-seconds", type=float)
    parser.add_argument("--deck", type=Path, help="html-deck directory to bind and validate")
    parser.add_argument("--deck-map", type=Path, help="defaults to DECK/presentation-map.json")
    args = parser.parse_args(argv)
    try:
        result = check_files(args.brief, args.script, args.out, args.audio_seconds,
                             args.command == "verify", args.deck, args.deck_map)
    except (ContractError, deck_bridge.DeckError) as exc:
        print(dump({"hard_errors": [str(exc)], "status": "blocked"}), end="")
        return 1
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(dump({"input_error": str(exc)}), end="")
        return 2
    print(dump(result), end="")
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
