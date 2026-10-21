"""Read html-deck's static files and keep stable, content-bound slide identities."""

from __future__ import annotations

import argparse
from collections import Counter
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit
import uuid


class DeckError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise DeckError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def json_read(path):
    def pairs(items):
        doc = {}
        for key, value in items:
            require(key not in doc, f"duplicate JSON key: {key}")
            doc[key] = value
        return doc
    def invalid(value):
        raise DeckError(f"non-finite JSON constant: {value}")
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=pairs, parse_constant=invalid)


def local_file(root, relative):
    require(isinstance(relative, str) and bool(relative), "relative resource path required")
    rel = Path(relative)
    require(not rel.is_absolute() and ".." not in rel.parts and "\\" not in relative,
            f"unsafe deck resource path: {relative}")
    path = (root / rel).resolve()
    require(path.is_relative_to(root.resolve()), f"deck resource escapes root: {relative}")
    require(path.is_file(), f"missing deck resource: {relative}")
    return path


def css_refs(value):
    value = re.sub(r"/\*.*?\*/", "", value, flags=re.S)
    urls = re.findall(r"url\(\s*['\"]?([^'\")]+)['\"]?\s*\)", value, flags=re.I)
    return urls + re.findall(r"@import\s+['\"]([^'\"]+)['\"]", value, flags=re.I)


class SlideParser(HTMLParser):
    """Extract static text only. This does not claim to measure rendered visibility."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.parts = {"title": [], "claim": [], "body": [], "css": []}
        self.references = []
        self.explicit_ids = []
        self.slide_roots = 0
        self.heading_depth = None
        self.heading_seen = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self.stack.append(tag)
            return
        if "slide" in attrs.get("class", "").split():
            self.slide_roots += 1
            if "data-slide-id" in attrs:
                self.explicit_ids.append(attrs["data-slide-id"])
        if tag in ("h1", "h2") and not self.heading_seen:
            self.heading_seen = True
            self.heading_depth = len(self.stack)
        if "style" in attrs:
            self.references.extend(css_refs(attrs["style"]))
        if tag in ("img", "script", "iframe", "source", "video", "audio", "embed") and attrs.get("src"):
            self.references.append(attrs["src"])
        if tag == "video" and attrs.get("poster"):
            self.references.append(attrs["poster"])
        if tag == "link" and attrs.get("href"):
            self.references.append(attrs["href"])
        if tag in ("image", "use"):
            ref = attrs.get("href", attrs.get("xlink:href"))
            if ref:
                self.references.append(ref)
        # srcset syntax includes data URLs with commas; don't silently miss dependencies.
        require(not attrs.get("srcset"), "srcset is unsupported; use a single local src or inline SVG")
        if tag not in ("area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"):
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            index = len(self.stack) - 1 - self.stack[::-1].index(tag)
            if self.heading_depth is not None and index <= self.heading_depth:
                self.heading_depth = None
            del self.stack[index:]

    def handle_data(self, data):
        if "style" in self.stack:
            self.parts["css"].append(data)
        if "title" in self.stack:
            self.parts["title"].append(data)
        if self.heading_depth is not None and not any(t in self.stack for t in ("script", "style")):
            self.parts["claim"].append(data)
        if "body" in self.stack and not any(t in self.stack for t in ("script", "style")):
            self.parts["body"].append(data)

    def finish(self):
        self.references.extend(css_refs("\n".join(self.parts["css"])))
        return {key: re.sub(r"\s+", " ", " ".join(value)).strip()
                for key, value in self.parts.items() if key != "css"}


def parse_html(path):
    parser = SlideParser()
    parser.feed(path.read_text(encoding="utf-8-sig"))
    parser.close()
    return parser, parser.finish()


def storyboard(markdown):
    match = re.search(r"^## storyboard\s*\n(.*?)(?=^## |\Z)", markdown, flags=re.M | re.S)
    require(match is not None, "deck.md needs the html-deck '## storyboard' section")
    rows = {}
    for line in match[1].splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [part.strip().replace(r"\|", "|") for part in re.split(r"(?<!\\)\|", line.strip())[1:-1]]
        if len(cells) < 2 or cells[0] == "id" or re.fullmatch(r"[-: ]+", cells[0]):
            continue
        key = cells[0].strip("`")
        require(key not in rows, f"duplicate storyboard id: {key}")
        rows[key] = cells[1]
    require(rows, "storyboard contains no slide rows")
    return rows


def validate_manifest(doc):
    require(isinstance(doc, dict) and type(doc.get("schema_version")) is int and doc["schema_version"] == 1,
            "deck map schema_version must be integer 1")
    require(doc.get("format") == "html-deck", "unsupported deck map format")
    require(isinstance(doc.get("slides"), list) and doc["slides"], "deck map needs slides")
    ids, files = set(), set()
    for position, slide in enumerate(doc["slides"], 1):
        require(isinstance(slide, dict), "slide map entry must be object")
        sid = slide.get("id")
        require(isinstance(sid, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", sid), "invalid stable slide id")
        require(isinstance(slide.get("file"), str), "slide file must be a string")
        require(sid not in ids and slide.get("file") not in files, "duplicate slide id or file")
        require(type(slide.get("position")) is int and slide["position"] == position, "slide positions must be contiguous")
        require(isinstance(slide.get("file"), str) and slide["file"].startswith("slides/"), "slide file must be in slides/")
        require(isinstance(slide.get("html_sha256"), str) and re.fullmatch(r"[a-f0-9]{64}", slide["html_sha256"]), "invalid slide hash")
        ids.add(sid)
        files.add(slide["file"])
    deps = doc.get("dependencies")
    require(isinstance(deps, dict), "dependencies must be an object")
    for path, value in deps.items():
        require(isinstance(path, str) and isinstance(value, str) and re.fullmatch(r"[a-f0-9]{64}", value), "invalid dependency digest")
    return doc


def scan(deck, previous=None):
    deck = deck.resolve()
    if previous is not None:
        validate_manifest(previous)
    dependencies = {}
    visited = set()

    def visit(path):
        relative = path.relative_to(deck).as_posix()
        checked = local_file(deck, relative)
        if relative in visited:
            return
        visited.add(relative)
        dependencies[relative] = digest(checked.read_bytes())
        references = []
        if checked.suffix.lower() == ".html":
            parser, _ = parse_html(checked)
            references = parser.references
        elif checked.suffix.lower() == ".css":
            references = css_refs(checked.read_text(encoding="utf-8-sig"))
        elif checked.suffix.lower() == ".svg":
            parser, _ = parse_html(checked)
            references = parser.references
        for reference in references:
            parts = urlsplit(reference.strip())
            if parts.scheme == "data" or (not parts.path and parts.fragment):
                continue
            require(not parts.scheme and not parts.netloc, f"external resource is unsupported: {reference}")
            refpath = unquote(parts.path)
            require(not refpath.startswith(("/", "\\")), f"absolute resource is unsupported: {reference}")
            target = (checked.parent / refpath).resolve()
            require(target.is_relative_to(deck), f"resource escapes deck: {reference}")
            visit(local_file(deck, target.relative_to(deck).as_posix()))

    for name in ("deck.md", "theme.css", "index.html"):
        visit(local_file(deck, name))
    rows = storyboard((deck / "deck.md").read_text(encoding="utf-8-sig"))
    slide_dir = deck / "slides"
    require(slide_dir.resolve().is_relative_to(deck), "slides directory escapes deck")
    paths = sorted(slide_dir.glob("*.html"))
    require(paths, "no slide HTML files")
    # html-deck-check writes this as JSON-compatible arrays. Never execute JavaScript.
    index = (deck / "index.html").read_text(encoding="utf-8-sig")
    match = re.search(r"// <slides>\s*const slides0\s*=\s*(\[.*?\]);\s*// </slides>", index, re.S)
    require(match is not None, "viewer slide list missing; run html-deck-check to synchronize it")
    try:
        viewer = json.loads(match[1])
    except json.JSONDecodeError as exc:
        raise DeckError("viewer slide list must be JSON-compatible; run html-deck-check") from exc
    expected_files = [path.relative_to(deck).as_posix() for path in paths]
    require(isinstance(viewer, list) and all(isinstance(item, list) and len(item) == 2 for item in viewer), "invalid viewer slide list")
    require([item[0] for item in viewer] == expected_files, "viewer order differs from files; run html-deck-check")
    slides = []
    used_rows = set()
    for position, path in enumerate(paths, 1):
        visit(path)
        parser, extracted = parse_html(path)
        require(parser.slide_roots == 1, f"expected one .slide root: {path.name}")
        require(extracted["title"] and extracted["claim"], f"slide title or heading missing: {path.name}")
        require(viewer[position - 1][1] == extracted["title"], f"viewer title stale: {path.name}; run html-deck-check")
        explicit = parser.explicit_ids[0] if parser.explicit_ids else None
        if parser.explicit_ids:
            require(isinstance(explicit, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", explicit),
                    f"invalid data-slide-id: {path.name}")
        number = re.match(r"^(\d+)(?:-|$)", path.stem)
        candidates = [explicit, path.stem, number[1] if number else None]
        row = next((key for key in candidates if key in rows), None)
        require(row is not None and row not in used_rows, f"no unique storyboard row for {path.name}")
        used_rows.add(row)
        slides.append({"id": explicit, "identity": "explicit" if explicit else "generated",
                       "position": position, "file": path.relative_to(deck).as_posix(),
                       "title": extracted["title"], "claim": extracted["claim"],
                       "storyboard_id": row, "storyboard_claim": rows[row],
                       "static_text": extracted["body"], "html_sha256": digest(path.read_bytes())})
    require(used_rows == set(rows), "storyboard has rows without a corresponding slide")
    old = previous["slides"] if previous else []
    occupied = {s["id"] for s in slides if s["id"] is not None}
    require(len(occupied) == sum(s["id"] is not None for s in slides), "duplicate explicit slide id")
    old_hash_counts = Counter(s["html_sha256"] for s in old)
    for slide in slides:
        if slide["id"] is None and old_hash_counts[slide["html_sha256"]] == 1:
            match = next(s for s in old if s["html_sha256"] == slide["html_sha256"])
            if match["id"] not in occupied:
                slide["id"] = match["id"]
                occupied.add(match["id"])
    for slide in slides:
        if slide["id"] is None:
            matches = [s for s in old if s["file"] == slide["file"] and s["id"] not in occupied]
            slide["id"] = matches[0]["id"] if matches else "s-" + uuid.uuid4().hex[:12]
            occupied.add(slide["id"])
    result = {"schema_version": 1, "format": "html-deck", "slides": slides, "dependencies": dependencies,
              "visual_validation": "not_assessed", "semantic_alignment": "not_assessed",
              "notice": "Static extraction only. Original materials and rendered slides require separate review."}
    return validate_manifest(result)


def load_current(deck, manifest_path):
    raw = manifest_path.read_bytes()
    manifest = validate_manifest(json_read(manifest_path))
    current = scan(deck, manifest)
    require(current["slides"] == manifest["slides"] and current["dependencies"] == manifest["dependencies"],
            "deck changed since sync; review slides and regenerate the deck map and affected script")
    return manifest, digest(raw)


def sync(deck, out):
    previous = json_read(out) if out.exists() else None
    result = scan(deck, previous)
    require(out.resolve() not in [(deck / name).resolve() for name in result["dependencies"]], "map would overwrite deck input")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes((json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("sync",))
    parser.add_argument("--deck", required=True, type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    try:
        out = args.out or args.deck / "presentation-map.json"
        result = sync(args.deck, out)
    except (DeckError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps({"map": str(out), "slides": [{k: s[k] for k in ("id", "position", "file", "claim")} for s in result["slides"]]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
