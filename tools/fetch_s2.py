"""Download the Q4_K_M GGUF and tokenizer with revision and SHA-256 verification."""

import argparse
import hashlib
import json
from pathlib import Path
import sys
import urllib.request

MODEL = "rodrigomt/s2-pro-gguf"
FILES = ("s2-pro-q4_k_m.gguf", "tokenizer.json")


def digest(path):
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            sha.update(block)
    return sha.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path(".runtime/models"))
    args = parser.parse_args()
    try:
        with urllib.request.urlopen(f"https://huggingface.co/api/models/{MODEL}?blobs=true", timeout=30) as response:
            info = json.load(response)
        revision = info["sha"]
        entries = {entry["rfilename"].lower(): entry for entry in info["siblings"]}
        args.directory.mkdir(parents=True, exist_ok=True)
        records = []
        for wanted in FILES:
            entry = entries.get(wanted)
            if entry is None:
                raise ValueError(f"{MODEL} does not contain {wanted}; review upstream files")
            name = entry["rfilename"]
            target = args.directory / wanted
            expected = entry.get("lfs", {}).get("sha256")
            if wanted.endswith(".gguf") and not expected:
                raise ValueError("The model has no published LFS SHA-256; refusing an unverified download")
            if target.exists() and expected and digest(target) == expected:
                print(f"Verified cached file: {target}")
            else:
                partial = target.with_suffix(target.suffix + ".part")
                url = f"https://huggingface.co/{MODEL}/resolve/{revision}/{name}"
                print(f"Downloading {name} from revision {revision}", flush=True)
                received, next_progress = 0, 256 * 1024 * 1024
                with urllib.request.urlopen(url, timeout=120) as response, partial.open("wb") as stream:
                    for block in iter(lambda: response.read(4 * 1024 * 1024), b""):
                        stream.write(block)
                        received += len(block)
                        if received >= next_progress:
                            print(f"  {received // (1024 * 1024)} MiB", flush=True)
                            next_progress += 256 * 1024 * 1024
                if expected and digest(partial) != expected:
                    raise ValueError(f"SHA-256 mismatch: {partial}")
                if entry.get("size") and partial.stat().st_size != entry["size"]:
                    raise ValueError(f"Download size mismatch: {partial}")
                partial.replace(target)
            records.append({"file": wanted, "sha256": digest(target)})
        (args.directory / "provenance.json").write_text(json.dumps({"model": MODEL,
            "revision": revision, "files": records}, indent=2) + "\n", encoding="utf-8")
        return 0
    except (OSError, ValueError, KeyError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
