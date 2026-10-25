"""Regression tests for the Action's evidence admissibility gate (AER-RC0 FIX-1).

`validate_evidence.py` is mirrored from `frank8ai/aer-runtime:action/validate_evidence.py`
and this file from `tests/contracts/test_evidence_admissibility.py`. Keep them identical;
the only edits here are the ACTION_DIR path and the `aer accept` invocation
literal (this action calls the standalone binary, aer-runtime calls `python -m aer`).

Each test names the counter-example it protects against. The first one is the
actual false-green path: a candidate commits a forged PASS, the acceptance run
crashes, and the check used to go green anyway.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ACTION_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ACTION_DIR))

from validate_evidence import FAIL, PASS, UNKNOWN, evaluate  # noqa: E402

BASE = "a" * 40
HEAD = "b" * 40


def _evidence(verdict: str, *, base: str = BASE, head: str = HEAD, reasons=None) -> dict:
    return {
        "schemaVersion": "change-acceptance/v1",
        "verdict": verdict,
        "reasons": list(reasons or []),
        "timestamp": "2026-10-25T00:00:00Z",
        "repository": "example/repo",
        "baseline": {"sha": base, "treeSha": "c" * 40},
        "candidate": {"sha": head, "treeSha": "d" * 40},
    }


class EvidenceAdmissibilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.path = self.root / "acceptance-evidence.json"

    def _write(self, payload) -> Path:
        if isinstance(payload, str):
            self.path.write_text(payload, encoding="utf-8")
        else:
            self.path.write_text(json.dumps(payload), encoding="utf-8")
        return self.path

    # -- counter-example 1: the false-green exploit ---------------------------

    def test_forged_pass_with_failed_execution_is_unknown(self) -> None:
        """A committed PASS must not survive a crashed acceptance run."""
        self._write(_evidence(PASS))
        verdict, reasons = evaluate(self.path, exit_code=1, base_sha=BASE, head_sha=HEAD)
        self.assertEqual(verdict, UNKNOWN)
        self.assertTrue(any("AER_EXECUTION_FAILED" in r for r in reasons))

    def test_forged_pass_with_timeout_exit_is_unknown(self) -> None:
        self._write(_evidence(PASS))
        verdict, _ = evaluate(self.path, exit_code=124, base_sha=BASE, head_sha=HEAD)
        self.assertEqual(verdict, UNKNOWN)

    # -- counter-example 2: evidence replayed from another commit ------------

    def test_pass_bound_to_wrong_head_is_unknown(self) -> None:
        self._write(_evidence(PASS, head="e" * 40))
        verdict, reasons = evaluate(self.path, exit_code=0, base_sha=BASE, head_sha=HEAD)
        self.assertEqual(verdict, UNKNOWN)
        self.assertTrue(any("candidate.sha" in r for r in reasons))

    def test_pass_bound_to_wrong_base_is_unknown(self) -> None:
        self._write(_evidence(PASS, base="f" * 40))
        verdict, reasons = evaluate(self.path, exit_code=0, base_sha=BASE, head_sha=HEAD)
        self.assertEqual(verdict, UNKNOWN)
        self.assertTrue(any("baseline.sha" in r for r in reasons))

    def test_pass_without_binding_fields_is_unknown(self) -> None:
        payload = _evidence(PASS)
        payload.pop("candidate")
        self._write(payload)
        verdict, _ = evaluate(self.path, exit_code=0, base_sha=BASE, head_sha=HEAD)
        self.assertEqual(verdict, UNKNOWN)

    # -- counter-example 3: structural validation failed --------------------

    def test_schema_failure_with_otherwise_valid_pass_is_unknown(self) -> None:
        """A document that fails schema validation is not admissible, even if it says PASS."""
        self._write(_evidence(PASS))
        verdict, reasons = evaluate(
            self.path, exit_code=0, base_sha=BASE, head_sha=HEAD, schema_exit_code=2
        )
        self.assertEqual(verdict, UNKNOWN)
        self.assertTrue(any("AER_EVIDENCE_SCHEMA_INVALID" in r for r in reasons))

    # -- counter-example 4: missing or unreadable evidence -------------------

    def test_missing_evidence_is_unknown(self) -> None:
        verdict, reasons = evaluate(self.path, exit_code=0, base_sha=BASE, head_sha=HEAD)
        self.assertEqual(verdict, UNKNOWN)
        self.assertTrue(any("AER_EVIDENCE_MISSING" in r for r in reasons))

    def test_malformed_json_is_unknown(self) -> None:
        self._write("{not json")
        verdict, reasons = evaluate(self.path, exit_code=0, base_sha=BASE, head_sha=HEAD)
        self.assertEqual(verdict, UNKNOWN)
        self.assertTrue(any("AER_EVIDENCE_UNREADABLE" in r for r in reasons))

    def test_unknown_verdict_string_is_unknown(self) -> None:
        self._write(_evidence("probably-fine"))
        verdict, reasons = evaluate(self.path, exit_code=0, base_sha=BASE, head_sha=HEAD)
        self.assertEqual(verdict, UNKNOWN)
        self.assertTrue(any("AER_EVIDENCE_MALFORMED" in r for r in reasons))

    def test_non_object_binding_section_is_unknown_not_crash(self) -> None:
        """A candidate may shape ``candidate`` however it likes; it must not crash the gate."""
        payload = _evidence(PASS)
        payload["candidate"] = HEAD
        payload["reasons"] = "single string, not a list"
        self._write(payload)
        verdict, _ = evaluate(self.path, exit_code=0, base_sha=BASE, head_sha=HEAD)
        self.assertEqual(verdict, UNKNOWN)

    # -- the happy paths still work -----------------------------------------

    def test_bound_pass_with_clean_exit_is_pass(self) -> None:
        self._write(_evidence(PASS, reasons=["reproduction: defect absent in candidate"]))
        verdict, reasons = evaluate(self.path, exit_code=0, base_sha=BASE, head_sha=HEAD)
        self.assertEqual(verdict, PASS)
        self.assertIn("reproduction: defect absent in candidate", reasons)

    def test_bound_fail_with_clean_exit_is_fail(self) -> None:
        self._write(_evidence(FAIL))
        verdict, _ = evaluate(self.path, exit_code=0, base_sha=BASE, head_sha=HEAD)
        self.assertEqual(verdict, FAIL)

    def test_non_pull_request_context_skips_binding(self) -> None:
        """Without base/head there is nothing to bind to; do not guess."""
        self._write(_evidence(PASS))
        verdict, _ = evaluate(self.path, exit_code=0, base_sha="", head_sha="")
        self.assertEqual(verdict, PASS)


class AdmissibilityCliTest(unittest.TestCase):
    """The Action branches on this process exit code, so pin the mapping."""

    def _run(self, payload, exit_code: int) -> subprocess.CompletedProcess:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "evidence.json"
            path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
            return subprocess.run(
                [
                    sys.executable,
                    str(ACTION_DIR / "validate_evidence.py"),
                    "--evidence", str(path),
                    "--exit-code", str(exit_code),
                    "--base-sha", BASE,
                    "--head-sha", HEAD,
                ],
                capture_output=True,
                text=True,
                check=False,
            )

    def test_pass_exits_zero(self) -> None:
        self.assertEqual(self._run(_evidence(PASS), 0).returncode, 0)

    def test_fail_exits_one(self) -> None:
        self.assertEqual(self._run(_evidence(FAIL), 0).returncode, 1)

    def test_inadmissible_exits_two(self) -> None:
        """The historical false green: PASS on disk, crashed run -> must not be 0."""
        self.assertEqual(self._run(_evidence(PASS), 1).returncode, 2)


class ActionWiringTest(unittest.TestCase):
    """The shell must delegate judging to the helper instead of re-deriving it."""

    def setUp(self) -> None:
        self.action = (ACTION_DIR / "action.yml").read_text(encoding="utf-8")

    def test_delegates_to_validate_evidence(self) -> None:
        self.assertIn("validate_evidence.py", self.action)

    def test_clears_stale_evidence_before_running(self) -> None:
        """A candidate can commit a forged PASS at the default path; it must not survive."""
        run_block = self.action.split("Run Executable Change Acceptance", 1)[1]
        self.assertIn('rm -f "$EVIDENCE"', run_block)
        self.assertLess(
            run_block.index('rm -f "$EVIDENCE"'),
            run_block.index("aer accept"),
            "stale evidence must be removed before the acceptance run",
        )

    def test_does_not_branch_on_verdict_string(self) -> None:
        self.assertNotIn('if [ "$VERDICT" = "PASS" ]', self.action)


if __name__ == "__main__":
    unittest.main()
