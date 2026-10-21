"""Compile a documented subset of W3C SSML 1.1 into speech and timing events."""

from dataclasses import asdict, dataclass, replace
import math
import re
import xml.etree.ElementTree as ET

NS = "http://www.w3.org/2001/10/synthesis"
LANG = "{http://www.w3.org/XML/1998/namespace}lang"
MAX_BYTES = 128_000


class SSMLError(ValueError):
    pass


@dataclass(frozen=True)
class Style:
    rate: float = 1.0
    pitch_st: float = 0.0
    volume_db: float = 0.0
    silent: bool = False


@dataclass(frozen=True)
class Speech:
    text: str
    style: Style


@dataclass(frozen=True)
class Pause:
    seconds: float


@dataclass(frozen=True)
class Mark:
    name: str


def bounded(value, low, high, label):
    if not math.isfinite(value) or not low <= value <= high:
        raise SSMLError(f"{label} must be between {low} and {high}")
    return value


def prosody(attrs, parent):
    style = parent
    if "rate" in attrs:
        value = attrs["rate"]
        names = {"x-slow": .5, "slow": .75, "medium": 1., "fast": 1.25,
                 "x-fast": 1.5, "default": 1.}
        if value in names:
            rate = names[value]
        elif re.fullmatch(r"\d+(?:\.\d+)?%", value):
            rate = float(value[:-1]) / 100
        else:
            raise SSMLError(f"Unsupported prosody rate: {value}")
        style = replace(style, rate=bounded(rate, .5, 2., "rate"))
    if "pitch" in attrs:
        value = attrs["pitch"]
        names = {"x-low": -6., "low": -3., "medium": 0., "high": 3.,
                 "x-high": 6., "default": 0.}
        if value in names:
            pitch = names[value]
        elif re.fullmatch(r"[+-]\d+(?:\.\d+)?st", value):
            pitch = parent.pitch_st + float(value[:-2])
        else:
            raise SSMLError(f"Unsupported pitch: {value}; use e.g. +2st")
        style = replace(style, pitch_st=bounded(pitch, -12., 12., "pitch"))
    if "volume" in attrs:
        value = attrs["volume"]
        names = {"x-soft": -9., "soft": -6., "medium": 0., "loud": 3.,
                 "x-loud": 6., "default": 0.}
        if value == "silent":
            style = replace(style, silent=True)
        else:
            if value in names:
                db = names[value]
            elif re.fullmatch(r"[+-]\d+(?:\.\d+)?dB", value):
                db = parent.volume_db + float(value[:-2])
            else:
                raise SSMLError(f"Unsupported volume: {value}")
            style = replace(style, volume_db=bounded(db, -24., 12., "volume"),
                            silent=parent.silent if value != "default" else False)
    return style


def compile_ssml(document):
    if len(document.encode("utf-8")) > MAX_BYTES:
        raise SSMLError(f"SSML exceeds {MAX_BYTES} bytes")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)\b", document, re.I):
        raise SSMLError("DTD and entity declarations are not supported")
    try:
        root = ET.fromstring(document)
    except ET.ParseError as exc:
        raise SSMLError(f"Invalid XML: {exc}") from exc
    if root.tag != f"{{{NS}}}speak":
        raise SSMLError(f"Root must be <speak xmlns=\"{NS}\">")
    if root.get("version") != "1.1" or not root.get(LANG):
        raise SSMLError('SSML 1.1 requires version="1.1" and xml:lang')

    events, warnings, marks = [], [], set()
    allowed = {
        "speak": {"version", LANG}, "s": {LANG}, "p": {LANG},
        "prosody": {"rate", "pitch", "volume", LANG},
        "emphasis": {"level", LANG}, "sub": {"alias", LANG},
        "break": {"time", "strength"}, "mark": {"name"},
    }

    def text(value, style):
        if not value:
            return
        value = re.sub(r"\s+", " ", value)
        if events and isinstance(events[-1], Speech) and events[-1].style == style:
            events[-1] = Speech(events[-1].text + value, style)
        elif value.strip():
            events.append(Speech(value, style))

    def walk(node, style, depth=0, ancestors=()):
        if depth > 24:
            raise SSMLError("SSML nesting exceeds 24 levels")
        if not isinstance(node.tag, str) or not node.tag.startswith(f"{{{NS}}}"):
            raise SSMLError("All elements must be in the W3C SSML namespace")
        tag = node.tag.split("}", 1)[1]
        if tag not in allowed or (tag == "speak" and depth):
            raise SSMLError(f"Unsupported element: {tag}")
        if tag in {"s", "p"} and ("s" in ancestors or "emphasis" in ancestors):
            raise SSMLError(f"{tag} is not allowed inside a sentence or emphasis")
        if tag == "p" and "p" in ancestors:
            raise SSMLError("Nested paragraphs are not allowed")
        unknown = set(node.attrib) - allowed[tag]
        if unknown:
            raise SSMLError(f"Unsupported {tag} attributes: {', '.join(sorted(unknown))}")
        if LANG in node.attrib and node.get(LANG) not in {"ja", "ja-JP", "en", "en-US"}:
            raise SSMLError("This profile supports xml:lang ja-JP and en-US only")
        if tag in {"break", "mark"}:
            if len(node) or (node.text and node.text.strip()):
                raise SSMLError(f"{tag} must be empty")
            if tag == "mark":
                name = node.get("name", "")
                if not name or name in marks:
                    raise SSMLError("mark names must be nonempty and unique")
                marks.add(name)
                events.append(Mark(name))
            else:
                strength = node.get("strength", "medium")
                strengths = {"none": 0., "x-weak": .08, "weak": .15,
                             "medium": .3, "strong": .6, "x-strong": 1.}
                if strength not in strengths:
                    raise SSMLError(f"Unsupported break strength: {strength}")
                seconds = strengths[strength]
                if "time" in node.attrib:
                    match = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s)", node.get("time"))
                    if not match:
                        raise SSMLError("break time must be e.g. 700ms or 0.7s")
                    seconds = float(match[1]) / (1000 if match[2] == "ms" else 1)
                events.append(Pause(bounded(seconds, 0., 10., "break seconds")))
            return
        if tag == "sub":
            if len(node) or not node.get("alias", "").strip():
                raise SSMLError("sub requires an alias and cannot contain nested elements")
            text(node.get("alias"), style)
            return
        if tag == "prosody":
            if not set(node.attrib) & {"rate", "pitch", "volume"}:
                raise SSMLError("prosody requires at least one supported prosody attribute")
            style = prosody(node.attrib, style)
        if tag == "emphasis":
            level = node.get("level", "moderate")
            settings = {"none": (1., 0.), "reduced": (1., -3.),
                        "moderate": (.95, 2.), "strong": (.9, 4.)}
            if level not in settings:
                raise SSMLError(f"Unsupported emphasis level: {level}")
            rate, db = settings[level]
            style = replace(style, rate=bounded(style.rate * rate, .5, 2., "rate"),
                            volume_db=bounded(style.volume_db + db, -24., 12., "volume"))
            if level not in {"none", "reduced"}:
                warnings.append("emphasis uses rate/volume approximation; linguistic emphasis is not guaranteed")
        text(node.text, style)
        for child in node:
            walk(child, style, depth + 1, ancestors + (tag,))
            text(child.tail, style)
        if tag in {"s", "p"}:
            # A zero-duration boundary keeps sentence requests separate without adding a hidden pause.
            events.append(Pause(0.))

    walk(root, Style())
    result = []
    for event in events:
        if isinstance(event, Speech):
            # shortcut: bound prompts at sentence boundaries; finer incremental text input needs a stateful TTS engine.
            parts = re.split(r"(?<=[。！？!?])\s*", event.text.strip())
            for part in parts:
                if not part:
                    continue
                if len(part) > 300:
                    raise SSMLError("A speech span exceeds 300 characters; split it with <s> or punctuation")
                if re.search(r"\[[^\]]*\]|<\|", part):
                    raise SSMLError("Fish control tokens in spoken text are not allowed; use SSML")
                result.append(Speech(part, event.style))
        else:
            result.append(event)
    if not any(isinstance(e, Speech) for e in result):
        raise SSMLError("SSML must contain spoken text")
    return result, sorted(set(warnings))


def event_dict(event):
    return {"type": type(event).__name__.lower(), **asdict(event)}
