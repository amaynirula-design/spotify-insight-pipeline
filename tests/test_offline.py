"""Offline system tests: no API key, no network. They drive the real pipeline code with the
rule-based mock provider and injected faults (rate limits, timeouts, malformed output, dropped
lines, unverifiable quotes) and then run the instructor's checker on the export.

    python -m unittest discover -s tests -v
"""

import csv
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import control, enrich, export, group, ingest, labels, memo, rank, verify  # noqa: E402
from pipeline.config import Config  # noqa: E402
from pipeline.db import DB  # noqa: E402
from pipeline.providers import MockProvider  # noqa: E402

COST_100 = ROOT / "data" / "cost_100.csv"
QUIET = lambda *a, **k: None


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Harness:
    def __init__(self, faults=""):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.cfg = Config(provider="mock", overrides={"limits.backoff_base_seconds": 0.001})
        self.db = DB(self.dir / "state.db")
        self.provider = MockProvider(faults)
        self.input_id, self.report = ingest.ingest(self.db, COST_100)
        self.run_dir = self.dir / "run"
        self.run_dir.mkdir()
        self.n = 0

    def enrich(self, **kw):
        self.n += 1
        return enrich.run_enrich(self.db, self.cfg, self.provider, self.input_id, self.run_dir, f"inv{self.n}", log=QUIET, **kw)

    def downstream(self):
        for stage in (verify.run_verify, group.run_group, memo.run_memo):
            stage(self.db, self.cfg, self.provider, self.input_id, self.run_dir, f"inv{self.n}", log=QUIET)
        rank.run_rank(self.db, self.cfg, self.input_id, self.run_dir, log=QUIET)
        return export.run_export(self.db, self.cfg, self.input_id, self.run_dir, full_path=COST_100, log=QUIET)

    def enrich_calls(self):
        return self.db.q("SELECT * FROM calls WHERE role='enrich' ORDER BY call_id")


class UnitTests(unittest.TestCase):
    def test_quote_must_be_exact_source_substring(self):
        text = "Great app.\nBut   it KEEPS crashing when I open a playlist!"
        self.assertEqual(labels.locate_quote(text, "KEEPS crashing"), "KEEPS crashing")
        self.assertEqual(labels.locate_quote(text, "but it keeps crashing"), "But   it KEEPS crashing")  # repaired to the source span
        self.assertEqual(labels.locate_quote(text, '"Great app. But it"'), "Great app.\nBut   it")
        self.assertIsNone(labels.locate_quote(text, "the app deleted my playlists"))
        self.assertIsNone(labels.locate_quote(text, "   "))
        self.assertIn(labels.fallback_quote(text), text)

    def test_every_returned_ref_is_validated(self):
        items = [{"text_key": f"k{i}", "review_id": f"id{i}", "review_text": t} for i, t in enumerate(
            ["Good", "Too many ads", "x" * 100 + " cannot log in at all", "Nice", "Bad", "Hmm"])]
        _, refs = enrich.build_input(items)
        output = "\n".join([
            "01|general|praise|2|1|0|",
            "02|ads|complaint|-1|2|0|",
            "02|ads|complaint|-1|2|0|",                    # duplicate ref -> rejected
            "03|login|complaint|-2|4|0|deleted my account",  # quote not in the source text
            "04|not_a_code|praise|2|1|0|",
            "05|general|complaint|-9|2|0|",                # out of range
            "77|general|praise|2|1|0|",                    # an ID that was never sent
        ])
        good, bad, stray = enrich.parse_output(output, refs)
        self.assertEqual(set(good), {"01"})
        self.assertEqual({k: v[0] for k, v in bad.items()}, {"02": "duplicate_ref", "03": "quote_not_in_text", "04": "unknown_code",
                                                              "05": "value_out_of_range", "06": "missing_from_output"})
        self.assertEqual(stray, 1)

    def test_severity_is_reconciled_with_intent_and_flagged(self):
        item = {"text_key": "k", "review_id": "id", "text": "Love it"}
        lab = {"code": "general", "intent": "praise", "sentiment": 2, "severity": 4, "flag": 0, "evidence_quote": "Love it", "quote_origin": "full_text"}
        row = enrich.completed_row(lab, item, "cfg", 1, 1)
        self.assertEqual((row[11], row[12]), (1, 1))  # severity 1, needs_review set

    def test_entities_are_literal_terms_only(self):
        self.assertEqual(labels.extract_entities("Too many ads, I will not buy Premium. YouTube Music is better"), ["ads", "premium", "youtube"])
        self.assertEqual(labels.extract_entities("Bahut accha"), [])

    def test_mean_uses_six_decimal_half_up(self):
        self.assertEqual(rank.mean_string(7, 3), "2.333333")
        self.assertEqual(rank.mean_string(5, 2), "2.500000")
        self.assertEqual(rank.mean_string(2000001, 2000000), "1.000001")

    def test_budget_reserves_before_dispatch(self):
        budget = control.Budget(1.0, already_spent=0.5)
        budget.reserve(0.3)
        with self.assertRaises(control.BudgetExceeded):
            budget.reserve(0.3)  # 0.5 spent + 0.3 reserved + 0.3 next > 1.0
        budget.settle(0.3, 0.1)
        budget.reserve(0.3)

    def test_memo_validator_rejects_typed_numbers_and_unknown_ids(self):
        claims = [{"claim_id": f"C00{i}", "issue_id": "billing.paywall", "metric": "complaint_count", "value": "10"} for i in (1, 2, 3)]
        evidence = {"billing.paywall": [{"review_id": "abc", "quote": "q"}]}
        ok = "Top is `billing.paywall` with {{C001}}, {{C002}}, {{C003}} in 2023 [review:abc]."
        self.assertEqual(memo.validate(ok, claims, [], evidence, {"billing.paywall"})[0], [])
        bad = "It has 4,512 complaints (37%) {{C001}} {{C002}} {{C009}} [review:zzz] `usability.fake`"
        problems = memo.validate(bad, claims, [], evidence, {"billing.paywall"})[0]
        self.assertEqual(len(problems), 6)


class SystemTests(unittest.TestCase):
    def test_faults_are_retried_and_every_row_is_accounted_for(self):
        h = Harness("http429=0.3,timeout=0.15,malformed=0.3,drop=0.08,badquote=0.4,seed=11")
        summary = h.enrich()
        counts = summary["counts_after"]
        self.assertEqual(counts["pending"], 0)
        self.assertEqual(counts["completed"] + counts["quarantined_nonempty"], 100)
        calls = h.enrich_calls()
        self.assertTrue(any(c["outcome"] == "failed" for c in calls), "failed attempts must be logged")
        self.assertTrue(any("transient" in c["attempt_kind"] for c in calls), "transient errors must be retried with backoff")
        self.assertTrue(any(c["attempt_kind"].startswith("retry") for c in calls), "invalid output must get its single retry")
        self.assertTrue(all(c["n_items"] <= 50 for c in calls))
        per_text = self.attempts_per_text(h)
        self.assertLessEqual(max(per_text.values()), 2, "invalid output is retried at most once")
        for rec, _ in export.iter_records(h.db, h.input_id, h.cfg.label_config):
            self.assertIn(rec["status"], ("completed", "quarantined"))
            if rec["status"] == "quarantined":
                self.assertTrue(rec["reason"])
        print(f"\n  fault run: {dict(summary['events'])}; calls={len(calls)}")

    @staticmethod
    def attempts_per_text(h):
        return {r["text_key"]: r["attempts"] for r in h.db.q("SELECT text_key, attempts FROM results")}

    def test_interrupt_then_resume_does_not_repay_for_completed_ids(self):
        h = Harness()
        first = h.enrich(stop_after_batches=1)
        self.assertGreater(first["counts_after"]["pending"], 0)
        self.assertEqual(first["checkpoint_written"], "checkpoint_before.json")
        before = set(json.loads((h.run_dir / "checkpoint_before.json").read_text())["completed_ids"])
        n_calls = len(h.enrich_calls())
        second = h.enrich()
        self.assertEqual(second["phase"], "resume")
        self.assertEqual(second["counts_after"]["pending"], 0)
        resumed = [c for c in h.enrich_calls()[n_calls:]]
        self.assertTrue(resumed)
        self.assertFalse(before & {rid for c in resumed for rid in json.loads(c["review_ids"])})
        after = set(json.loads((h.run_dir / "checkpoint_after.json").read_text())["completed_ids"])
        self.assertTrue(before < after)
        n_calls = len(h.enrich_calls())
        warm = h.enrich()
        self.assertEqual(len(h.enrich_calls()), n_calls, "a warm run must make zero new enrichment calls")
        self.assertEqual(warm["requests_planned"], 0)

    def test_spend_cap_stops_admitting_work_and_keeps_progress(self):
        h = Harness()
        original = control.load_rates
        control.load_rates = lambda path=None: {("mock-rule-v0", "standard", item): 1e-5 for item in ("input_uncached", "input_cached", "output")}
        try:
            role = h.cfg.role("enrich")
            worst = ((len(h.cfg.instructions("enrich")) + 5000) // 3 + 50 + role["max_output_tokens"]) * 1e-5
            cap = round(worst * 1.25, 4)  # room for the first request's reservation, not for a second one
            summary = h.enrich(budget_usd=cap)
        finally:
            control.load_rates = original
        self.assertIn("budget_cap", summary["stop_reason"])
        self.assertGreater(summary["counts_after"]["completed"], 0)
        self.assertGreater(summary["counts_after"]["pending"], 0)
        self.assertLessEqual(summary["spent_usd_total_ledger"], cap)

    def test_interrupted_in_flight_call_is_reconciled_as_unknown(self):
        h = Harness()
        h.db.open_call(invocation="crashed", input_id=h.input_id, role="enrich", phase="initial", attempt_kind="first", provider="mock",
                       model="mock-rule-v0", effort="none", tier="standard", stage_config=h.cfg.label_config, review_ids=[], n_items=0,
                       worker=0, reserved_usd=0.01)
        summary = h.enrich()
        self.assertEqual(summary["interrupted_calls_reconciled"], 1)
        self.assertEqual(h.db.one("SELECT outcome FROM calls WHERE invocation='crashed'")["outcome"], "unknown")

    def test_provider_config_failure_stops_instead_of_quarantining(self):
        h = Harness("http400=1.0")
        summary = h.enrich()
        self.assertIn("fatal", summary["stop_reason"])
        self.assertEqual(summary["counts_after"]["quarantined_nonempty"], 0)
        self.assertEqual(summary["counts_after"]["pending"], 100)

    def test_full_mock_run_passes_the_instructor_checker(self):
        h = Harness("http429=0.2,badquote=0.3,seed=3")  # faults that are recoverable within one retry
        h.enrich(stop_after_batches=1)
        h.enrich()
        h.downstream()
        checker = load_module(ROOT / "check_submission.py", "check_submission")
        reference = checker.reference(COST_100, COST_100)
        result = checker.audit(h.run_dir / "grading", reference)
        self.assertEqual(result["issue_counts"], {}, result["examples"])
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["coverage"]["expected"], 100)
        # ranking regenerates identically from saved records + membership, without the DB or a model
        from pipeline.__main__ import main
        self.assertEqual(main(["rerank", "--grading", str(h.run_dir / "grading")]), 0)

    def test_checker_flags_nonempty_quarantines_as_unfinished(self):
        h = Harness("malformed=0.6,seed=1")  # output is invalid on the first try and again on the single retry
        h.enrich()
        h.downstream()
        quarantined = h.db.status_counts(h.input_id, h.cfg.label_config)["quarantined_nonempty"]
        self.assertGreater(quarantined, 0)
        checker = load_module(ROOT / "check_submission.py", "check_submission")
        result = checker.audit(h.run_dir / "grading", checker.reference(COST_100, COST_100))
        self.assertEqual(result["coverage"]["quarantined"], quarantined)  # visible, with reasons, and not counted as classified
        self.assertIn("unfinished_classification", result["issue_counts"])
        self.assertLess(result["working_coverage_point_candidate"], 1.0)

    def test_arbitrary_csv_with_empty_duplicate_and_multiline_text(self):
        tmp = tempfile.TemporaryDirectory()
        source = Path(tmp.name) / "fresh.csv"
        with source.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["review_id", "review_text", "review_rating", "review_likes", "app_version", "review_timestamp"])
            w.writerow(["a1", "Too many ads", "1", "0", "", "2023-01-01 00:00:00"])
            w.writerow(["a2", "   ", "3", "0", "8.8", "2023-01-02 00:00:00"])          # whitespace only -> empty_review_text
            w.writerow(["a3", "Too many ads", "2", "0", "", "2023-02-01 00:00:00"])      # exact duplicate text -> cache reuse
            w.writerow(["a4", "Line one\nline two, cannot log in", "1", "4", "", "2023-02-02 00:00:00"])
            w.writerow(["a5", "too many ads", "2", "0", "", "2023-02-03 00:00:00"])      # different case: NOT a duplicate
        cfg, db = Config(provider="mock"), DB(Path(tmp.name) / "s.db")
        input_id, report = ingest.ingest(db, source)
        self.assertEqual(report["counts"]["empty_review_text"], 1)
        self.assertEqual(report["classification_scope"]["distinct_nonempty_texts"], 3)
        run_dir = Path(tmp.name) / "run"
        run_dir.mkdir()
        enrich.run_enrich(db, cfg, MockProvider(""), input_id, run_dir, "t", log=QUIET)
        records = {r["review_id"]: r for r, _ in export.iter_records(db, input_id, cfg.label_config)}
        self.assertEqual((records["a2"]["status"], records["a2"]["reason"]), ("quarantined", "empty_review_text"))
        self.assertEqual(records["a3"]["cache_source_id"], "a1")
        self.assertNotIn("cache_source_id", records["a5"])
        self.assertIn("\n", db.one("SELECT review_text FROM reviews WHERE review_id='a4'")["review_text"])
        sent = [rid for c in db.q("SELECT review_ids FROM calls") for rid in json.loads(c["review_ids"])]
        self.assertEqual(sorted(sent), ["a1", "a4", "a5"])  # the empty row and the duplicate were never sent to a model

    def test_calculator_rates_scale_cost_but_not_time(self):
        calc = load_module(ROOT / "cost" / "calculator.py", "calculator")
        h = Harness()
        h.enrich()
        h.downstream()
        folder = h.dir / "cost"
        folder.mkdir()
        calls = [calc.call_record(r, "cold") for r in calc.db_calls(h.db.path)]
        (folder / "pilot_calls.jsonl").write_text("".join(json.dumps(c) + "\n" for c in calls))
        (folder / "pilot_records.jsonl").write_text((h.run_dir / "grading" / "records.jsonl").read_text())
        calc.write_csv(folder / "usage.csv", calc.USAGE_COLUMNS, calc.usage_from_calls(calls))
        run = {"wall_seconds_end_to_end": 40.0, "stage_wall_seconds": {"enrich": 20.0, "verify": 5.0, "group": 9.0, "memo": 4.0}, "workers": 1}
        (folder / "pilot_meta.json").write_text(json.dumps({
            "input_file": "cost_100.csv", "input_sha256": "x", "rows": 100, "distinct_texts": 100, "provider": "mock", "tier": "standard",
            "executed_at": "test", "stage_configs": {r: h.cfg.stage_config(r) for r in calc.ROLES}, "config": h.cfg.raw,
            "runs": {"cold": run, "warm": dict(run, wall_seconds_end_to_end=1.0, stage_wall_seconds={})}}))
        rates_path = folder / "rates.csv"
        with (ROOT / "cost" / "rates.csv").open() as f:
            rows = list(csv.DictReader(f))
        for row in rows:
            if row["model"] == "mock-rule-v0":
                row["price"] = {"input_uncached": "0.10", "input_cached": "0.01", "output": "0.50"}[row["item"]]
        calc.write_csv(rates_path, list(rows[0]), rows)
        a = json.loads((ROOT / "cost" / "assumptions.json").read_text())
        _, one = calc.build_report(folder, calc.load_rates(rates_path, 1), rates_path, a, "pilot", 1)
        _, two = calc.build_report(folder, calc.load_rates(rates_path, 2), rates_path, a, "pilot", 2)
        self.assertGreater(one["api_subtotal_measured"], 0)
        self.assertAlmostEqual(two["api_subtotal_measured"], 2 * one["api_subtotal_measured"])
        self.assertEqual(one["measured_wall_seconds"], two["measured_wall_seconds"])
        self.assertAlmostEqual(two["projections"][0]["api_total"], 2 * one["projections"][0]["api_total"])
        self.assertEqual(one["projections"][0]["wall_seconds"], two["projections"][0]["wall_seconds"])
        a["projected"]["distinct_nonempty_texts"] *= 2  # changing projected volume leaves the measured pilot untouched
        _, big = calc.build_report(folder, calc.load_rates(rates_path, 1), rates_path, a, "pilot", 1)
        self.assertEqual(big["api_subtotal_measured"], one["api_subtotal_measured"])
        self.assertGreater(big["projections"][0]["api_total"], one["projections"][0]["api_total"])
        self.assertTrue(one["usage_matches_call_log"])


if __name__ == "__main__":
    unittest.main()
