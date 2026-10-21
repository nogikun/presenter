"""Interactive speech: sentence splitting and the `say` loop."""

import re
import sys

from . import voices
from .audio import AudioError, render
from .fillers import Fillers
from .ssml import Speech, Style

SENTENCE = re.compile(r"[^。！？!?]+[。！？!?]*")


def clauses(sentence, max_chars):
    """Split a sentence longer than max_chars at 、 outside 「」/（）, or between 」「 in a list of
    titles, regrouping clauses so each piece stays near max_chars. Long requests run slower and
    need a long head start."""
    if not max_chars or len(sentence) <= max_chars:
        return [sentence]
    found, depth, current = [], 0, ""
    for i, ch in enumerate(sentence):
        current += ch
        depth += (ch in "「『（(【") - (ch in "」』）)】")
        between_titles = ch in "」』" and sentence[i + 1:i + 2] in ("「", "『")
        if (ch == "、" or between_titles) and depth <= 0:
            found.append(current)
            current = ""
    if current:
        found.append(current)
    pieces = []
    for clause in found:
        if pieces and len(pieces[-1]) + len(clause) <= max_chars:
            pieces[-1] += clause
        else:
            pieces.append(clause)
    return pieces


def segments(line, split_first_clause, max_chars=0):
    """The line's sentences, long ones cut into clauses; optionally the first clause (up to 、)
    alone, so its audio comes sooner."""
    parts = [p for s in SENTENCE.findall(line) if s.strip() for p in clauses(s.strip(), max_chars)]
    if split_first_clause and parts:
        head, comma, rest = parts[0].partition("、")
        if comma and rest:
            parts[:1] = [head + comma, rest]
    return parts


def say(backend, text, output):
    """Speak the given text once, or each typed line until Ctrl+Z/Ctrl+D; "/voice NAME" switches."""
    settings = backend.config.get("say", {})
    fillers = Fillers(backend, settings) if settings.get("filler", False) else None
    while True:
        try:
            line = text or input("> ").strip()
        except EOFError:
            return 0
        if not text and line.startswith("/voice"):
            try:
                voices.set_voice(backend.config, line[len("/voice"):].strip())
                print(f"voice: {backend.config.get('voice', 'default')}")
                if fillers:
                    fillers.prepare()
            except AudioError as exc:
                print(f"ERROR: {exc}", file=sys.stderr)
            continue
        # One request per sentence: long requests run slower (RTF 1.65 vs ~1.5 measured).
        # Each sentence gets its own server head start, so pauses fall between sentences,
        # never inside one. An optional filler plays only if the first audio is late.
        # Punctuation-only input ("！？") yields no sentences and is skipped.
        sentences = segments(line, settings.get("split_first_clause", False),
                             int(settings.get("max_chars", 40)))
        if sentences:
            try:
                report = render([Speech(s, Style()) for s in sentences], backend, output, play=True,
                                force=True, filler=fillers and fillers.choose(sentences[0]),
                                filler_after=fillers.after if fillers else 0)
                if fillers and not text:
                    fillers.heard(report)
            except AudioError as exc:
                if text:
                    raise
                print(f"ERROR: {exc}", file=sys.stderr)
        if text:
            return 0
