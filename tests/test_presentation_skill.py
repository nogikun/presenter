import copy
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHECKER_PATH = ROOT / "skills/presentation-ja/scripts/presentation_check.py"
spec = importlib.util.spec_from_file_location("presentation_check_test", CHECKER_PATH)
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
eval_spec = importlib.util.spec_from_file_location("presentation_evaluate_test", ROOT / "dev/presentation-ja/evaluate.py")
evaluation = importlib.util.module_from_spec(eval_spec)
eval_spec.loader.exec_module(evaluation)


def inputs():
    brief = {"schema_version": 1, "audience": "同僚", "prior_knowledge": "novice",
             "goal": "試験の結果と条件を知る", "duration_seconds": 60, "delivery": "live",
             "sources": [{"id": "S1", "text": "同じ端末で100文書を処理し、中央値は7秒だった。本番は未確認。",
                          "origin": "synthetic"}], "must_preserve": ["本番は未確認"]}
    body = "同じ端末で100文書を処理し、中央値は7秒でした。本番は未確認です。"
    script = {"schema_version": 1, "main_message": "検証条件内での結果を報告します。",
              "framework": "SDS", "rationale": "短い報告", "assumptions": [], "questions": [],
              "glossary": [], "segments": [{"id": "result", "role": "body", "text": body,
                 "cue": "図を示す。これは本文ではありません。999999文字でも計数しない。",
                 "pause_after_seconds": 2,
                 "evidence": [{"claim": body, "source_id": "S1", "quote": brief["sources"][0]["text"]}]}]}
    return brief, script


class PresentationContractTests(unittest.TestCase):
    def test_valid_contract_has_no_quality_claim(self):
        result = checker.assess(*inputs())
        self.assertEqual(result["hard_errors"], [])
        self.assertEqual(result["semantic_fidelity"], "not_assessed")
        self.assertEqual(result["human_listening"], "not_assessed")
        self.assertNotIn("quality_score", result)

    def test_number_adjacent_to_japanese_is_checked(self):
        brief, script = inputs()
        script["segments"][0]["text"] += "別の結果は99秒です。"
        findings = checker.assess(brief, script)["review_findings"]
        self.assertTrue(any(f.get("excerpt") == "99" for f in findings))

    def test_fullwidth_number_is_normalized_for_comparison(self):
        self.assertEqual(checker.numeric_tokens("中央値１００文書で７秒、１２．５％。"), {"100", "7", "12.5%"})

    def test_decimal_is_not_split_into_integer_parts(self):
        self.assertEqual(checker.numeric_tokens("値は12.5。"), {"12.5"})

    def test_identifier_digit_is_not_a_measurement(self):
        self.assertEqual(checker.numeric_tokens("S1 HTTP2 v1.2"), set())

    def test_unknown_source_is_a_hard_error(self):
        brief, script = inputs()
        script["segments"][0]["evidence"][0]["source_id"] = "unknown"
        with self.assertRaises(checker.ContractError):
            checker.assess(brief, script)

    def test_fabricated_quote_is_a_hard_error(self):
        brief, script = inputs()
        script["segments"][0]["evidence"][0]["quote"] = "本番も7秒だった。"
        with self.assertRaises(checker.ContractError):
            checker.assess(brief, script)

    def test_claim_must_be_in_spoken_body(self):
        brief, script = inputs()
        script["segments"][0]["evidence"][0]["claim"] = "資料にはあるが本文にはない"
        with self.assertRaises(checker.ContractError):
            checker.assess(brief, script)

    def test_matching_quote_cannot_prove_semantic_entailment(self):
        brief, script = inputs()
        bad_claim = "本番も確認済みです。"
        script["segments"][0]["text"] = bad_claim
        script["segments"][0]["evidence"][0]["claim"] = bad_claim
        result = checker.assess(brief, script)
        self.assertEqual(result["semantic_fidelity"], "not_assessed")

    def test_markup_not_allowed_in_speech(self):
        for markup in ("<break time='1s'/>", "```", "# 見出し", "- 箇条書き"):
            brief, script = inputs()
            script["segments"][0]["text"] += "\n" + markup
            with self.subTest(markup=markup), self.assertRaises(checker.ContractError):
                checker.assess(brief, script)

    def test_malformed_structure_fails_without_type_errors(self):
        for field, value in (("segments", []), ("segments", [False]), ("glossary", [1]),
                             ("assumptions", "none"), ("schema_version", True)):
            brief, script = inputs()
            script[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(checker.ContractError):
                checker.assess(brief, script)

    def test_duplicate_segment_or_source_is_rejected(self):
        brief, script = inputs()
        script["segments"].append(copy.deepcopy(script["segments"][0]))
        with self.assertRaises(checker.ContractError):
            checker.assess(brief, script)
        brief, script = inputs()
        brief["sources"].append(copy.deepcopy(brief["sources"][0]))
        with self.assertRaises(checker.ContractError):
            checker.assess(brief, script)

    def test_invalid_pace_is_rejected(self):
        for pace in ({"slow": 300, "fast": 200}, {"slow": 0, "fast": 200}, {"slow": True, "fast": 200}):
            brief, script = inputs()
            brief["pace_cpm"] = pace
            with self.subTest(pace=pace), self.assertRaises(checker.ContractError):
                checker.assess(brief, script)

    def test_nonfinite_duration_and_boolean_are_rejected(self):
        for duration in (True, 0, -1, float("inf"), float("nan")):
            brief, script = inputs()
            brief["duration_seconds"] = duration
            with self.subTest(duration=duration), self.assertRaises(checker.ContractError):
                checker.assess(brief, script)

    def test_duplicate_json_keys_and_constants_are_rejected(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(checker.ContractError):
                checker.loads(raw)

    def test_cues_and_metadata_not_counted_as_speech(self):
        brief, script = inputs()
        first = checker.assess(brief, script)
        script["main_message"] += "演出" * 1000
        script["segments"][0]["cue"] += "指示" * 1000
        self.assertEqual(first["timing"], checker.assess(brief, script)["timing"])
        self.assertNotIn("999999", checker.render(script)["manuscript.txt"])

    def test_measured_audio_is_whole_duration_not_double_pauses(self):
        brief, script = inputs()
        result = checker.assess(brief, script, audio_seconds=59)
        self.assertEqual(result["timing"]["measured_audio_seconds"], 59)
        self.assertFalse(any(f["rule"].endswith("over_budget") for f in result["review_findings"]))

    def test_measured_overrun_remains_a_review_finding(self):
        result = checker.assess(*inputs(), audio_seconds=61)
        self.assertIn("measured_over_budget", [f["rule"] for f in result["review_findings"]])

    def test_receipt_detects_input_and_rendered_output_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            brief, script = inputs()
            bp, sp, out = folder / "brief.json", folder / "script.json", folder / "checked"
            bp.write_text(checker.dump(brief), encoding="utf-8")
            sp.write_text(checker.dump(script), encoding="utf-8")
            checker.check_files(bp, sp, out)
            self.assertTrue(checker.check_files(bp, sp, out, verify=True)["verified"])
            (out / "manuscript.txt").write_text("changed", encoding="utf-8")
            with self.assertRaises(checker.ContractError):
                checker.check_files(bp, sp, out, verify=True)
            checker.check_files(bp, sp, out)
            brief["goal"] = "changed goal"
            bp.write_text(checker.dump(brief), encoding="utf-8")
            with self.assertRaises(checker.ContractError):
                checker.check_files(bp, sp, out, verify=True)

    def test_out_cannot_overwrite_input(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            bp = folder / "manuscript.txt"
            sp = folder / "script.json"
            bp.write_text(checker.dump(inputs()[0]), encoding="utf-8")
            sp.write_text(checker.dump(inputs()[1]), encoding="utf-8")
            with self.assertRaises(checker.ContractError):
                checker.check_files(bp, sp, folder)

    def test_no_source_facts_are_not_marked_verified(self):
        brief, script = inputs()
        brief["sources"] = []
        script["segments"][0]["evidence"] = []
        result = checker.assess(brief, script)
        self.assertEqual(result["semantic_fidelity"], "not_assessed")
        self.assertIn("no_evidence_mapping", [f["rule"] for f in result["review_findings"]])

    def test_missing_input_exit_code(self):
        with redirect_stdout(io.StringIO()) as output:
            code = checker.main(["check", "--brief", "nonexistent-input-file", "--script", "also-missing",
                                 "--out", "unused-output"])
        self.assertEqual(code, 2)
        self.assertIn("input_error", output.getvalue())


class EvaluationHarnessTests(unittest.TestCase):
    def paired_trial(self, out):
        evaluation.prepare(out)
        for brief_path in out.glob("*/brief.json"):
            brief = evaluation.read(brief_path)
            source = brief["sources"][0]
            _, script = inputs()
            script["segments"][0].update(text=source["text"], evidence=[
                {"claim": source["text"], "source_id": source["id"], "quote": source["text"]}])
            for method in ("with_skill", "without_skill"):
                evaluation.write(brief_path.parent / method / "outputs/script.json", script)
        evaluation.collect(out)
        evaluation.blind(out, 17)
        cases = []
        for path in out.glob("*/eval_metadata.json"):
            meta = evaluation.read(path)
            candidate = {"critical_issues": [], "dimensions": {
                d: {"score": 3, "evidence": "synthetic unit-test evidence", "suggestion": "none"}
                for d in evaluation.DIMENSIONS}, "expectations": [
                {"text": a, "passed": True, "evidence": "synthetic unit-test evidence"}
                for a in meta["assertions"]]}
            cases.append({"name": path.parent.name, "winner": "TIE", "reasoning": "unit test fixture",
                          "candidates": {"A": candidate, "B": copy.deepcopy(candidate)}})
        for order, name in ((["A", "B"], "judge-ab.json"), (["B", "A"], "judge-ba.json")):
            evaluation.write(out / name, {"evaluator_kind": "independent-model", "model": "synthetic-unit-test",
                                          "presentation_order": order, "cases": copy.deepcopy(cases)})

    def test_prepare_has_separate_splits_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "dev"
            manifest = evaluation.prepare(out)
            self.assertEqual(len(list(out.glob("*/brief.json"))), 3)
            self.assertEqual(manifest["snapshot_phase"], "before_generation")
            self.assertFalse((out / "holdout-untrusted-material").exists())
            with self.assertRaises(ValueError):
                evaluation.prepare(out)

    def test_missing_outputs_remain_in_denominator(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "trial"
            evaluation.prepare(out)
            result = evaluation.collect(out)
            self.assertEqual(result["missing_or_invalid"], 6)
            self.assertEqual(len(result["runs"]), 6)
            self.assertIsNone(result["quality_score"])
            with self.assertRaises(ValueError):
                evaluation.blind(out, 1)

    def test_dataset_provenance_and_fixture_formats(self):
        data = evaluation.read(evaluation.DATASET)
        self.assertEqual(len(data["evals"]), 6)
        self.assertEqual({e["split"] for e in data["evals"]}, {"development", "holdout"})
        for case in data["evals"]:
            self.assertTrue(case["assertions"])
            self.assertTrue(all(s["origin"] == "synthetic" for s in case["brief"]["sources"]))

    def test_judge_disagreement_is_not_cherry_picked(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "trial"
            self.paired_trial(out)
            a = evaluation.read(out / "judge-ab.json")
            b = evaluation.read(out / "judge-ba.json")
            a["cases"][0]["winner"], b["cases"][0]["winner"] = "A", "B"
            evaluation.write(out / "judge-ab.json", a)
            evaluation.write(out / "judge-ba.json", b)
            result = evaluation.summarize(out)
            changed = a["cases"][0]["name"]
            self.assertEqual(next(c for c in result["agreements"] if c["case"] == changed)["resolved_preference"],
                             "needs_adjudication")
            benchmark = evaluation.read(out / "benchmark.json")
            self.assertEqual(len(benchmark["runs"]), 6)
            self.assertTrue(all(r["result"]["tokens"] is None for r in benchmark["runs"]))

    def test_blind_reroll_or_tampering_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "trial"
            self.paired_trial(out)
            with self.assertRaises(ValueError):
                evaluation.blind(out, 18)
            blind_brief = next((out / "blind").glob("*/brief.json"))
            changed = evaluation.read(blind_brief)
            changed["goal"] = "tampered"
            evaluation.write(blind_brief, changed)
            with self.assertRaises(evaluation.checker.ContractError):
                evaluation.summarize(out)

    def test_false_string_is_not_accepted_as_true_judgment(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "trial"
            self.paired_trial(out)
            a = evaluation.read(out / "judge-ab.json")
            a["cases"][0]["candidates"]["A"]["expectations"][0]["passed"] = "false"
            evaluation.write(out / "judge-ab.json", a)
            with self.assertRaises(evaluation.checker.ContractError):
                evaluation.summarize(out)

    def test_trigger_set_is_labeled_unmeasured(self):
        data = evaluation.read(ROOT / "dev/presentation-ja/trigger-evals.json")
        self.assertEqual(data["status"], "not_run")
        self.assertEqual(len(data["queries"]), 20)
        self.assertEqual(sum(q["should_trigger"] for q in data["queries"]), 10)

    def test_frontmatter_and_reference_links(self):
        skill = CHECKER_PATH.parents[1]
        body = (skill / "SKILL.md").read_text(encoding="utf-8")
        self.assertTrue(body.startswith("---\nname: presentation-ja\n"))
        self.assertLess(len(body.splitlines()), 500)
        import re
        for relative in re.findall(r"\]\((references/[^)]+)\)", body):
            self.assertTrue((skill / relative).is_file(), relative)


if __name__ == "__main__":
    unittest.main()
