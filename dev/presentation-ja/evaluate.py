"""Prepare paired trials and collect local outputs. Never calls or grades an LLM."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import random
import statistics
import sys

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / "skills" / "presentation-ja"
DATASET = Path(__file__).with_name("evals.json")
spec = importlib.util.spec_from_file_location("presentation_check", SKILL / "scripts" / "presentation_check.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(checker.dump(value), encoding="utf-8")


def read(path):
    return checker.loads(path.read_bytes())


def prepare(out, split="development", model="unknown"):
    if out.exists():
        raise ValueError("Use a new run directory; existing evidence is never regenerated in place.")
    data = read(DATASET)
    cases = [e for e in data["evals"] if e["split"] == split]
    out.mkdir(parents=True)
    for case in cases:
        directory = out / case["name"]
        write(directory / "brief.json", case["brief"])
        write(directory / "eval_metadata.json", {"eval_id": case["id"], "eval_name": case["name"],
                                                "prompt": case["prompt"], "assertions": case["assertions"]})
        for method in ("with_skill", "without_skill"):
            (directory / method / "outputs").mkdir(parents=True)
    manifest = {"schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
                "dataset_sha256": checker.sha(DATASET.read_bytes()), "policy_sha256": checker.policy_hashes(),
                "checker_sha256": checker.sha((SKILL / "scripts" / "presentation_check.py").read_bytes()),
                "split": split, "model": model, "snapshot_phase": "before_generation",
                "trials_per_case": 1, "generation_settings": "record actual host settings separately",
                "human_listening": "not_assessed", "holdout_status": "not_assessed"}
    write(out / "manifest.json", manifest)
    return manifest


def collect(out):
    manifest = read(out / "manifest.json")
    checker.require(manifest["policy_sha256"] == checker.policy_hashes(), "skill policy changed since snapshot")
    checker.require(manifest["dataset_sha256"] == checker.sha(DATASET.read_bytes()), "dataset changed since snapshot")
    checker.require(manifest["checker_sha256"] == checker.sha((SKILL / "scripts" / "presentation_check.py").read_bytes()),
                    "checker changed since snapshot")
    runs = []
    for meta_path in sorted(out.glob("*/eval_metadata.json")):
        meta, directory = read(meta_path), meta_path.parent
        for method in ("with_skill", "without_skill"):
            run = directory / method
            script = run / "outputs" / "script.json"
            entry = {"eval_id": meta["eval_id"], "eval_name": meta["eval_name"],
                     "configuration": method, "time_seconds": None, "tokens": None,
                     "generation_status": "missing", "semantic_fidelity": "not_assessed"}
            if script.exists():
                try:
                    result = checker.check_files(directory / "brief.json", script, run / "checks")
                    checker.check_files(directory / "brief.json", script, run / "checks", verify=True)
                    # The public review view lists both raw JSON and the exact spoken output.
                    spoken = (run / "checks" / "manuscript.txt").read_bytes()
                    (run / "outputs" / "manuscript.txt").write_bytes(spoken)
                    entry.update(generation_status="completed", mechanical_status=result["status"],
                                 review_findings=result["review_findings"], timing=result["timing"],
                                 script_sha256=checker.sha(script.read_bytes()))
                except (checker.ContractError, OSError, ValueError) as exc:
                    entry.update(generation_status="invalid", hard_errors=[str(exc)])
            runs.append(entry)
    result = {"schema_version": 1, "runs": runs,
              "missing_or_invalid": sum(r["generation_status"] != "completed" for r in runs),
              "quality_score": None, "human_listening": "not_assessed",
              "notice": "Mechanical findings are review candidates, not quality scores. Missing runs remain visible."}
    write(out / "mechanical-summary.json", result)
    return result


def blind(out, seed):
    if (out / "blind-key.json").exists():
        raise ValueError("Blind allocation already exists; do not reroll.")
    summary = read(out / "mechanical-summary.json")
    if summary["missing_or_invalid"]:
        raise ValueError("All paired generation runs must exist and be valid before blind allocation.")
    rng, key = random.Random(seed), {}
    for meta_path in sorted(out.glob("*/eval_metadata.json")):
        directory, meta = meta_path.parent, read(meta_path)
        methods = ["with_skill", "without_skill"]
        rng.shuffle(methods)
        key[directory.name] = dict(zip(("A", "B"), methods))
        target = out / "blind" / directory.name
        write(target / "brief.json", read(directory / "brief.json"))
        write(target / "task.json", {"prompt": meta["prompt"], "assertions": meta["assertions"]})
        for label, method in key[directory.name].items():
            doc = read(directory / method / "outputs" / "script.json")
            # Do not give the judge the method label, rationale or the writer's self-assessment.
            write(target / f"{label}.json", {k: doc[k] for k in
                  ("main_message", "assumptions", "questions", "glossary", "segments")})
    write(out / "blind-key.json", {"seed": seed, "mapping": key})
    return {"cases": list(key), "judge_input": str(out / "blind"),
            "instructions": "Independent judges read only blind inputs and rubric; reverse presentation order in a second fresh context."}


DIMENSIONS = ("fidelity", "audience_fit", "structure", "spoken_japanese", "pacing", "delivery_readiness")


def summarize(out):
    allocation = read(out / "blind-key.json")["mapping"]
    mechanical = read(out / "mechanical-summary.json")
    checker.require(mechanical["missing_or_invalid"] == 0, "Missing or invalid generations cannot be omitted")
    judges = [read(out / name) for name in ("judge-ab.json", "judge-ba.json")]
    checker.require(judges[0].get("presentation_order") == ["A", "B"], "First judge must use A then B")
    checker.require(judges[1].get("presentation_order") == ["B", "A"], "Second judge must use B then A")
    indexed = []
    for judge in judges:
        checker.require(judge.get("evaluator_kind") == "independent-model", "Independent judge required")
        cases = judge.get("cases", [])
        checker.require(isinstance(cases, list), "Judge cases must be an array")
        mapping = {case["name"]: case for case in cases}
        checker.require(len(mapping) == len(cases) and set(mapping) == set(allocation), "Judge coverage mismatch")
        indexed.append(mapping)
    agreements, runs = [], []
    for case_name, labels in allocation.items():
        directory = out / case_name
        meta = read(directory / "eval_metadata.json")
        checker.require(read(out / "blind" / case_name / "brief.json") == read(directory / "brief.json"),
                        "Blind brief changed")
        checker.require(read(out / "blind" / case_name / "task.json") ==
                        {"prompt": meta["prompt"], "assertions": meta["assertions"]}, "Blind task changed")
        cases = [index[case_name] for index in indexed]
        wins = [case["winner"] for case in cases]
        checker.require(all(w in ("A", "B", "TIE") for w in wins), "Invalid judge winner")
        agreement = wins[0] == wins[1]
        resolved = labels[wins[0]] if agreement and wins[0] != "TIE" else ("tie" if agreement else "needs_adjudication")
        agreements.append({"case": case_name, "order_judgments": wins,
                           "agreement": agreement, "resolved_preference": resolved})
        for label, method in labels.items():
            script_path = directory / method / "outputs" / "script.json"
            checker.check_files(directory / "brief.json", script_path, directory / method / "checks", verify=True)
            original = read(script_path)
            stripped = {k: original[k] for k in ("main_message", "assumptions", "questions", "glossary", "segments")}
            checker.require(read(out / "blind" / case_name / f"{label}.json") == stripped, "Blind output changed")
            candidates = [case["candidates"][label] for case in cases]
            for candidate in candidates:
                checker.require(isinstance(candidate.get("critical_issues"), list), "Critical issues must be an array")
                for dim in DIMENSIONS:
                    detail = candidate["dimensions"][dim]
                    score = detail.get("score")
                    checker.require(score is None or (type(score) is int and 1 <= score <= 5), "Invalid dimension score")
                    checker.text(detail.get("evidence"), "dimension evidence")
            graded = []
            for expectation in meta["assertions"]:
                decisions = []
                for candidate in candidates:
                    matches = [e for e in candidate["expectations"] if e["text"] == expectation]
                    checker.require(len(matches) == 1, "Every frozen expectation must be graded exactly once")
                    item = matches[0]
                    checker.require(type(item.get("passed")) is bool, "Expectation verdict must be boolean")
                    checker.text(item.get("evidence"), "expectation evidence")
                    decisions.append(item)
                graded.append({"text": expectation, "passed": all(d["passed"] for d in decisions),
                               "evidence": " | ".join(d["evidence"] for d in decisions),
                               "judge_agreement": decisions[0]["passed"] == decisions[1]["passed"]})
            passed, total = sum(e["passed"] for e in graded), len(graded)
            critical = [issue for c in candidates for issue in c["critical_issues"]]
            grading = {"expectations": graded,
                       "summary": {"passed": passed, "failed": total - passed, "total": total, "pass_rate": passed / total},
                       "critical_issues": critical, "human_listening": "not_assessed",
                       "notes": ["Conservative intersection of two model judges; not a human comprehension measure."]}
            write(directory / method / "grading.json", grading)
            runs.append({"eval_id": meta["eval_id"], "eval_name": case_name, "configuration": method,
                         "run_number": 1, "result": {**grading["summary"], "time_seconds": None, "tokens": None,
                                                     "critical_issue_count": len(critical)},
                         "expectations": graded})
    runs.sort(key=lambda r: (r["eval_id"], r["configuration"] != "with_skill"))
    summaries = {}
    for method in ("with_skill", "without_skill"):
        values = [r["result"]["pass_rate"] for r in runs if r["configuration"] == method]
        summaries[method] = {"pass_rate": {"mean": statistics.mean(values),
                                          "stddev": statistics.stdev(values) if len(values) > 1 else 0,
                                          "min": min(values), "max": max(values)}}
    benchmark = {"metadata": {"skill_name": "presentation-ja", "executor_model": read(out / "manifest.json")["model"],
                               "runs_per_configuration": 1, "timestamp": datetime.now(timezone.utc).isoformat(),
                               "evals_run": sorted(set(r["eval_id"] for r in runs))},
                 "runs": runs, "run_summary": summaries,
                 "notes": ["Pass rate is frozen task assertions only; no aggregate quality score.",
                           "Time/token telemetry unavailable; null is not zero.",
                           "All input materials are synthetic. Human listening and holdout evaluation are not assessed.",
                           "Single trial per case, same model family; exploratory, not superiority evidence."]}
    write(out / "benchmark.json", benchmark)
    result = {"agreements": agreements, "human_listening": "not_assessed", "quality_score": None}
    write(out / "comparison-summary.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "collect", "blind", "summarize"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--split", choices=("development", "holdout"), default="development")
    parser.add_argument("--model", default="unknown")
    parser.add_argument("--seed", type=int, default=1729)
    args = parser.parse_args(argv)
    try:
        if args.command == "prepare":
            result = prepare(args.out, args.split, args.model)
        elif args.command == "collect":
            result = collect(args.out)
        elif args.command == "blind":
            result = blind(args.out, args.seed)
        else:
            result = summarize(args.out)
    except (OSError, ValueError, KeyError) as exc:
        print(checker.dump({"error": str(exc)}), end="")
        return 1
    print(checker.dump(result), end="")
    return 1 if args.command == "collect" and result["missing_or_invalid"] else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
