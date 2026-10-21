"""Run local checks and optionally measure actual TTS; save all diagnostic results."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def decode(data):
    # wsl.exe writes UTF-16 when stdout is redirected on some Windows versions.
    if b"\x00" in data[:200]:
        value = data.decode("utf-16", errors="replace")
    else:
        value = data.decode("utf-8-sig", errors="replace")
    return value.replace("\r\n", "\n").replace("\r", "")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synthesize", action="store_true", help="Requires an already running local TTS server")
    parser.add_argument("--input", type=Path, default=ROOT / "examples/presentation.ssml")
    parser.add_argument("--config", type=Path, default=Path("presenter.local.toml"))
    parser.add_argument("--runs", type=int, choices=range(1, 6), default=1)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    folder = ROOT / ".runtime" / "validation" / stamp
    folder.mkdir(parents=True)
    temporary = ROOT / ".runtime" / "tmp"
    temporary.mkdir(exist_ok=True)
    env = dict(os.environ, PYTHONUTF8="1", TEMP=str(temporary), TMP=str(temporary))
    steps = []
    report = {"created_utc": stamp, "python": sys.executable, "steps": steps, "audio": []}

    def run(name, command, timeout=60):
        print(f"\n[{name}]", flush=True)
        try:
            process = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, timeout=timeout)
            result = {"name": name, "command": command, "exit_code": process.returncode,
                      "stdout": decode(process.stdout), "stderr": decode(process.stderr)}
        except (OSError, subprocess.TimeoutExpired) as exc:
            result = {"name": name, "command": command, "exit_code": -1, "error": str(exc)}
        steps.append(result)
        for key in ("stdout", "stderr", "error"):
            if result.get(key):
                print(result[key], flush=True)
        return result["exit_code"] == 0

    ok = True
    try:
        if os.name == "nt":
            run("wsl-status", ["wsl.exe", "--status"], timeout=15)
            run("wsl-distributions", ["wsl.exe", "--list", "--verbose"], timeout=15)
        if compiler := shutil.which("nvcc"):
            run("cuda-compiler-on-path", [compiler, "--version"])
        ok = run("tests", [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"])
        cli = [sys.executable, "-m", "presenter_tts", "--config", str(args.config)]
        ok = run("ssml-plan", cli + ["plan", str(args.input)]) and ok
        ok = run("doctor", cli + ["doctor"]) and ok
        if args.synthesize and ok:
            output_folder = ROOT / "outputs" / ("validation-" + stamp)
            output_folder.mkdir(parents=True)
            for index in range(args.runs):
                output = output_folder / f"run-{index + 1}.wav"
                if not run(f"synthesis-{index + 1}", cli + ["speak", str(args.input), "--output", str(output)], timeout=1800):
                    ok = False
                    break
                metrics = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
                report["audio"].append({"wav": str(output), "timing": metrics})
        report["checks_passed"] = ok
        # Passing non-synthesis checks says nothing about model speed or GPU synthesis.
        report["synthesis_verified"] = bool(report["audio"]) and ok
        report["note"] = "Inspect doctor output and test skips. No GPU peak-memory measurement or listening evaluation is performed."
    finally:
        path = folder / "report.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"\nSaved results: {path}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
