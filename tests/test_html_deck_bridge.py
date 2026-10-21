import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("deck_bound_checker_test", ROOT / "skills/presentation-ja/scripts/presentation_check.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)
bridge = checker.deck_bridge


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def deck_fixture(root, explicit=True):
    root.mkdir(parents=True, exist_ok=True)
    slides = [("01-overview", "overview", "順位で統合します"), ("02-limits", "limits", "条件を確認します")]
    write(root / "theme.css", ".slide { width:1600px; height:900px; }\n")
    for stem, sid, claim in slides:
        attr = f' data-slide-id="{sid}"' if explicit else ""
        write(root / f"slides/{stem}.html",
              '<!doctype html><html lang="ja"><head><meta charset="utf-8">'
              f'<title>{claim}</title><link rel="stylesheet" href="../theme.css">'
              '<style>h2{font-size:54px}</style></head><body>'
              f'<article class="slide"{attr}><h2>{claim}</h2><p>説明です。</p></article></body></html>')
    sync_fixture_viewer(root)
    return root


def sync_fixture_viewer(root):
    """Test-fixture construction only; production viewer sync belongs to html-deck-check."""
    files = sorted((root / "slides").glob("*.html"))
    titles = [bridge.parse_html(p)[1]["title"] for p in files]
    entries = [[p.relative_to(root).as_posix(), title] for p, title in zip(files, titles)]
    write(root / "index.html", '<!doctype html><html><body><script>\n// <slides>\nconst slides0 = '
          + json.dumps(entries, ensure_ascii=False) + ';\n// </slides>\n</script></body></html>')
    rows = []
    for path, title in zip(files, titles):
        key = path.stem.split('-')[0]
        rows.append(f"| {key} | {title} | 説明 | 図 | synthetic | 問い → 答え |")
    write(root / "deck.md", '# 合成の検査用デッキ\n\n## goal\n仕組みを説明する\n\n## storyboard\n'
          '| id | claim | job | visual | evidence | 問い |\n| --- | --- | --- | --- | --- | --- |\n'
          + '\n'.join(rows) + '\n')


def bound_inputs(root):
    map_path = root / "presentation-map.json"
    deck_map = bridge.sync(root, map_path)
    brief = {"schema_version": 1, "audience": "同僚", "prior_knowledge": "novice", "goal": "仕組みと条件を知る",
             "duration_seconds": 120, "delivery": "live", "must_preserve": [],
             "sources": [{"id": "S1", "text": "順位で統合します。条件を確認します。", "origin": "synthetic"}],
             "slide_targets": {slide["id"]: 60 for slide in deck_map["slides"]}}
    script = {"schema_version": 1, "main_message": "仕組みと条件を説明します。", "framework": "SDS",
              "rationale": "短い説明", "assumptions": [], "questions": [], "glossary": [],
              "deck_binding": {"map_sha256": checker.sha(map_path.read_bytes())}, "segments": []}
    for slide in deck_map["slides"]:
        body = slide["claim"] + "。"
        script["segments"].append({"id": "n-" + slide["id"], "role": "body", "slide_id": slide["id"],
            "slide_sha256": slide["html_sha256"], "text": body, "cue": "図を示して切り替える",
            "pause_after_seconds": 1, "evidence": [{"claim": body, "quote": body, "source_id": "S1"}]})
    bp, sp = root / "narration/brief.json", root / "narration/script.json"
    write(bp, checker.dump(brief))
    write(sp, checker.dump(script))
    return brief, script, deck_map, bp, sp


class HtmlDeckBridgeTests(unittest.TestCase):
    def test_sync_extracts_real_html_deck_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            root = deck_fixture(Path(temp) / "deck")
            manifest = bridge.sync(root, root / "presentation-map.json")
            self.assertEqual([s["id"] for s in manifest["slides"]], ["overview", "limits"])
            self.assertEqual(manifest["slides"][0]["storyboard_id"], "01")
            self.assertEqual(manifest["slides"][0]["claim"], "順位で統合します")
            self.assertIn("theme.css", manifest["dependencies"])
            self.assertNotIn("font-size", manifest["slides"][0]["static_text"])

    def test_unchanged_sync_is_idempotent(self):
        with tempfile.TemporaryDirectory() as temp:
            root = deck_fixture(Path(temp) / "deck", explicit=False)
            target = root / "presentation-map.json"
            bridge.sync(root, target)
            before = target.read_bytes()
            bridge.sync(root, target)
            self.assertEqual(before, target.read_bytes())

    def test_empty_explicit_id_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = deck_fixture(Path(temp) / "deck")
            path = root / "slides/01-overview.html"
            write(path, path.read_text(encoding="utf-8").replace('data-slide-id="overview"', 'data-slide-id=""'))
            with self.assertRaises(bridge.DeckError):
                bridge.scan(root)

    def test_malformed_map_is_rejected_as_contract_error(self):
        with tempfile.TemporaryDirectory() as temp:
            root = deck_fixture(Path(temp) / "deck")
            manifest = bridge.scan(root)
            manifest["slides"][0]["file"] = []
            with self.assertRaises(bridge.DeckError):
                bridge.validate_manifest(manifest)
            target = root / "invalid-map.json"
            for value in ('{"x":NaN}', '{"x":1,"x":2}'):
                write(target, value)
                with self.assertRaises(bridge.DeckError):
                    bridge.json_read(target)

    def test_implicit_ids_survive_exact_content_renaming_and_reordering(self):
        with tempfile.TemporaryDirectory() as temp:
            root = deck_fixture(Path(temp) / "deck", explicit=False)
            target = root / "presentation-map.json"
            old = bridge.sync(root, target)
            first, second = root / old["slides"][0]["file"], root / old["slides"][1]["file"]
            first_bytes, second_bytes = first.read_bytes(), second.read_bytes()
            first.write_bytes(second_bytes)
            second.write_bytes(first_bytes)
            sync_fixture_viewer(root)
            new = bridge.sync(root, target)
            self.assertEqual([s["id"] for s in new["slides"]], [s["id"] for s in reversed(old["slides"])])

    def test_explicit_id_survives_edit_and_rename(self):
        with tempfile.TemporaryDirectory() as temp:
            root = deck_fixture(Path(temp) / "deck")
            target = root / "presentation-map.json"
            bridge.sync(root, target)
            original = root / "slides/01-overview.html"
            value = original.read_text(encoding="utf-8").replace("説明です。", "説明を追加しました。")
            renamed = root / "slides/03-overview.html"
            write(renamed, value)
            original.unlink()
            sync_fixture_viewer(root)
            new = bridge.sync(root, target)
            self.assertEqual(new["slides"][1]["id"], "overview")

    def test_implicit_edit_and_rename_is_not_guessed_by_position(self):
        with tempfile.TemporaryDirectory() as temp:
            root = deck_fixture(Path(temp) / "deck", explicit=False)
            target = root / "presentation-map.json"
            old = bridge.sync(root, target)
            old_id = old["slides"][0]["id"]
            original = root / old["slides"][0]["file"]
            write(root / "slides/03-new.html", original.read_text(encoding="utf-8").replace("説明です。", "異なる説明です。"))
            original.unlink()
            sync_fixture_viewer(root)
            new = bridge.sync(root, target)
            self.assertNotIn(old_id, {s["id"] for s in new["slides"]})

    def test_same_file_edit_keeps_id_but_marks_content_stale(self):
        with tempfile.TemporaryDirectory() as temp:
            root = deck_fixture(Path(temp) / "deck", explicit=False)
            target = root / "presentation-map.json"
            old = bridge.sync(root, target)
            path = root / old["slides"][0]["file"]
            write(path, path.read_text(encoding="utf-8").replace("説明です。", "変更です。"))
            with self.assertRaises(bridge.DeckError):
                bridge.load_current(root, target)
            new = bridge.sync(root, target)
            self.assertEqual(new["slides"][0]["id"], old["slides"][0]["id"])
            self.assertNotEqual(new["slides"][0]["html_sha256"], old["slides"][0]["html_sha256"])

    def test_stale_viewer_order_or_title_blocks_sync(self):
        with tempfile.TemporaryDirectory() as temp:
            root = deck_fixture(Path(temp) / "deck")
            original = root / "index.html"
            value = original.read_text(encoding="utf-8").replace("slides/01-overview.html", "slides/99-missing.html")
            write(original, value)
            with self.assertRaises(bridge.DeckError):
                bridge.scan(root)

    def test_missing_storyboard_row_or_duplicate_explicit_id_blocks_sync(self):
        with tempfile.TemporaryDirectory() as temp:
            root = deck_fixture(Path(temp) / "deck")
            row = root / "deck.md"
            write(row, row.read_text(encoding="utf-8").replace("| 02 |", "| 99 |"))
            with self.assertRaises(bridge.DeckError):
                bridge.scan(root)
            sync_fixture_viewer(root)
            slide = root / "slides/02-limits.html"
            write(slide, slide.read_text(encoding="utf-8").replace('data-slide-id="limits"', 'data-slide-id="overview"'))
            with self.assertRaises(bridge.DeckError):
                bridge.scan(root)

    def test_stale_css_and_local_image_dependencies_are_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = deck_fixture(Path(temp) / "deck")
            image = root / "assets/diagram.png"
            image.parent.mkdir()
            image.write_bytes(b"synthetic image bytes; no rendering claim")
            slide = root / "slides/01-overview.html"
            write(slide, slide.read_text(encoding="utf-8").replace("</article>", '<img src="../assets/diagram.png"></article>'))
            target = root / "presentation-map.json"
            manifest = bridge.sync(root, target)
            self.assertIn("assets/diagram.png", manifest["dependencies"])
            image.write_bytes(b"changed bytes")
            with self.assertRaises(bridge.DeckError):
                bridge.load_current(root, target)
            bridge.sync(root, target)
            write(root / "theme.css", ".slide{color:red}")
            with self.assertRaises(bridge.DeckError):
                bridge.load_current(root, target)

    def test_css_import_and_font_url_are_snapshotted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = deck_fixture(Path(temp) / "deck")
            write(root / "theme.css", '@import "styles/colors.css"; @font-face { src:url(fonts/sample.woff2); }')
            write(root / "styles/colors.css", ".slide{color:black}")
            write(root / "fonts/sample.woff2", "synthetic-font-fixture")
            result = bridge.scan(root)
            self.assertTrue({"styles/colors.css", "fonts/sample.woff2"}.issubset(result["dependencies"]))

    def test_external_resource_and_path_escape_are_not_read(self):
        for reference in ("https://example.com/image.png", "../../outside.png", "/absolute.png", "%2e%2e/%2e%2e/outside.png"):
            with self.subTest(reference=reference), tempfile.TemporaryDirectory() as temp:
                root = deck_fixture(Path(temp) / "deck")
                slide = root / "slides/01-overview.html"
                write(slide, slide.read_text(encoding="utf-8").replace("</article>", f'<img src="{reference}"></article>'))
                with self.assertRaises(bridge.DeckError):
                    bridge.scan(root)

    def test_loop_reports_and_generated_notes_do_not_invalidate_map(self):
        with tempfile.TemporaryDirectory() as temp:
            root = deck_fixture(Path(temp) / "deck")
            target = root / "presentation-map.json"
            bridge.sync(root, target)
            write(root / ".loop/round-1/report.json", '{"synthetic":true}')
            write(root / "narration/checked/speaker-notes.md", "notes")
            bridge.load_current(root, target)


class SlideBoundScriptTests(unittest.TestCase):
    def test_end_to_end_mapping_notes_timing_and_receipt(self):
        with tempfile.TemporaryDirectory() as temp:
            root = deck_fixture(Path(temp) / "deck")
            brief, script, manifest, bp, sp = bound_inputs(root)
            out = root / "narration/checked"
            result = checker.check_files(bp, sp, out, deck_path=root)
            self.assertEqual(result["slide_alignment"], "structural_only")
            self.assertEqual([s["slide_id"] for s in result["slides"]], ["overview", "limits"])
            notes = (out / "speaker-notes.md").read_text(encoding="utf-8")
            self.assertIn("スライド 1: 順位で統合します", notes)
            self.assertIn("slides/02-limits.html", notes)
            self.assertNotIn("図を示して", (out / "manuscript.txt").read_text(encoding="utf-8"))
            self.assertEqual((out / "by-slide/overview.txt").read_text(encoding="utf-8"), "順位で統合します。\n")
            self.assertEqual(result["slides"][1]["estimated_start_seconds"], result["slides"][0]["estimated_seconds"])
            self.assertTrue(checker.check_files(bp, sp, out, verify=True, deck_path=root)["verified"])
            self.assertEqual(result["semantic_fidelity"], "not_assessed")

    def test_multiple_segments_on_one_slide_are_allowed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = deck_fixture(Path(temp) / "deck")
            brief, script, manifest, bp, sp = bound_inputs(root)
            segment = copy.deepcopy(script["segments"][0])
            segment["id"] += "-second"
            script["segments"].insert(1, segment)
            result = checker.assess(brief, script, deck_map=manifest, map_sha256=script["deck_binding"]["map_sha256"])
            self.assertEqual(len(result["slides"][0]["segment_ids"]), 2)

    def test_unknown_missing_or_out_of_order_slides_fail(self):
        for mutation in ("unknown", "missing", "order", "content"):
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as temp:
                root = deck_fixture(Path(temp) / "deck")
                brief, script, manifest, bp, sp = bound_inputs(root)
                if mutation == "unknown":
                    script["segments"][0]["slide_id"] = "unknown"
                elif mutation == "missing":
                    script["segments"].pop()
                elif mutation == "order":
                    script["segments"].reverse()
                else:
                    script["segments"][0]["slide_sha256"] = "0" * 64
                with self.assertRaises(checker.ContractError):
                    checker.assess(brief, script, deck_map=manifest, map_sha256=script["deck_binding"]["map_sha256"])

    def test_bound_script_cannot_silently_fall_back_to_standalone(self):
        with tempfile.TemporaryDirectory() as temp:
            root = deck_fixture(Path(temp) / "deck")
            brief, script, _, _, _ = bound_inputs(root)
            with self.assertRaises(checker.ContractError):
                checker.assess(brief, script)

    def test_resync_does_not_approve_stale_segment_content(self):
        with tempfile.TemporaryDirectory() as temp:
            root = deck_fixture(Path(temp) / "deck")
            brief, script, _, bp, sp = bound_inputs(root)
            slide = root / "slides/01-overview.html"
            write(slide, slide.read_text(encoding="utf-8").replace("説明です。", "数値を変更しました。"))
            bridge.sync(root, root / "presentation-map.json")
            script["deck_binding"]["map_sha256"] = checker.sha((root / "presentation-map.json").read_bytes())
            write(sp, checker.dump(script))
            with self.assertRaises(checker.ContractError):
                checker.check_files(bp, sp, root / "checked", deck_path=root)

    def test_slide_budget_validation_and_review_finding(self):
        with tempfile.TemporaryDirectory() as temp:
            root = deck_fixture(Path(temp) / "deck")
            brief, script, manifest, _, _ = bound_inputs(root)
            brief["slide_targets"] = {"overview": 1, "limits": 60}
            result = checker.assess(brief, script, deck_map=manifest, map_sha256=script["deck_binding"]["map_sha256"])
            self.assertIn("slide_estimated_over_budget", [f["rule"] for f in result["review_findings"]])
            brief["slide_targets"] = {"overview": 80, "limits": 80}
            with self.assertRaises(checker.ContractError):
                checker.assess(brief, script, deck_map=manifest, map_sha256=script["deck_binding"]["map_sha256"])

    def test_asset_change_invalidates_final_verify(self):
        with tempfile.TemporaryDirectory() as temp:
            root = deck_fixture(Path(temp) / "deck")
            _, _, _, bp, sp = bound_inputs(root)
            out = root / "checked"
            checker.check_files(bp, sp, out, deck_path=root)
            write(root / "theme.css", ".slide{font-size:30px}")
            with self.assertRaises(bridge.DeckError):
                checker.check_files(bp, sp, out, verify=True, deck_path=root)

    def test_per_slide_output_tampering_is_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = deck_fixture(Path(temp) / "deck")
            _, _, _, bp, sp = bound_inputs(root)
            out = root / "checked"
            checker.check_files(bp, sp, out, deck_path=root)
            write(out / "by-slide/limits.txt", "changed")
            with self.assertRaises(checker.ContractError):
                checker.check_files(bp, sp, out, verify=True, deck_path=root)

    def test_removed_slide_requires_new_output_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            root = deck_fixture(Path(temp) / "deck")
            brief, script, _, bp, sp = bound_inputs(root)
            out = root / "checked"
            checker.check_files(bp, sp, out, deck_path=root)
            (root / "slides/02-limits.html").unlink()
            sync_fixture_viewer(root)
            bridge.sync(root, root / "presentation-map.json")
            brief["slide_targets"].pop("limits")
            script["segments"].pop()
            script["deck_binding"]["map_sha256"] = checker.sha((root / "presentation-map.json").read_bytes())
            write(bp, checker.dump(brief))
            write(sp, checker.dump(script))
            with self.assertRaisesRegex(checker.ContractError, "new output directory"):
                checker.check_files(bp, sp, out, deck_path=root)
            self.assertTrue((out / "by-slide/limits.txt").exists())
            fresh = root / "checked-v2"
            checker.check_files(bp, sp, fresh, deck_path=root)
            self.assertFalse((fresh / "by-slide/limits.txt").exists())
            self.assertTrue(checker.check_files(bp, sp, fresh, verify=True, deck_path=root)["verified"])


if __name__ == "__main__":
    unittest.main()
