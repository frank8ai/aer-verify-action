#!/usr/bin/env python3
"""Decide whether an AER acceptance evidence document is admissible.

Why this exists
---------------
The GitHub Action used to take the verdict straight out of the evidence JSON and
ignore the exit code of ``aer accept``. Two consequences, both reachable by a
candidate pull request:

1. A candidate can commit a forged ``{"verdict": "PASS"}`` at the default
   evidence path (``artifacts/aer/acceptance-evidence.json``, which is not
   gitignored). If ``aer accept`` then fails for any reason, the stale file is
   still on disk and the check goes green.
2. A crashed acceptance run leaves whatever file happened to be there, and the
   verdict is read from it instead of from the process outcome.

So admissibility has two independent conditions, and both must hold:

* **Process truth** — ``aer accept`` exited 0. A non-zero exit means the engine
  did not complete; no document on disk is admissible, whatever it says.
* **Binding truth** — the document's ``baseline.sha`` / ``candidate.sha`` equal
  the commits actually under review. Without this, evidence produced for a
  different commit pair can be replayed.

Anything that cannot be positively established is ``UNKNOWN``. ``UNKNOWN`` is
non-success: the check goes red and a human decides.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PASS = "PASS"
FAIL = "FAIL"
UNKNOWN = "UNKNOWN"
VALID_VERDICTS = (PASS, FAIL, UNKNOWN)

EXIT_BY_VERDICT = {PASS: 0, FAIL: 1, UNKNOWN: 2}


def evaluate(
    evidence_path: Path,
    *,
    exit_code: int,
    base_sha: str = "",
    head_sha: str = "",
    schema_exit_code: int = 0,
) -> tuple[str, list[str]]:
    """Return ``(verdict, reasons)`` for one acceptance run.

    ``base_sha`` / ``head_sha`` may be empty in non pull-request contexts; the
    binding check is then skipped rather than guessed.

    ``schema_exit_code`` is the outcome of an optional structural validation
    (``aer verify-evidence``). A failed structural check is also inadmissible.
    """
    if exit_code != 0:
        return UNKNOWN, [
            f"AER_EXECUTION_FAILED: aer accept exited {exit_code}; "
            "evidence is not admissible regardless of its contents"
        ]

    if schema_exit_code != 0:
        return UNKNOWN, [
            f"AER_EVIDENCE_SCHEMA_INVALID: verify-evidence exited {schema_exit_code}"
        ]

    if not evidence_path.is_file():
        return UNKNOWN, [
            "AER_EVIDENCE_MISSING: aer accept exited 0 but produced no evidence document"
        ]

    try:
        document = json.loads(evidence_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:  # noqa: BLE001 - any parse failure is inconclusive
        return UNKNOWN, [f"AER_EVIDENCE_UNREADABLE: {exc}"]

    if not isinstance(document, dict):
        return UNKNOWN, ["AER_EVIDENCE_MALFORMED: root is not a JSON object"]

    verdict = document.get("verdict")
    if verdict not in VALID_VERDICTS:
        return UNKNOWN, [f"AER_EVIDENCE_MALFORMED: verdict {verdict!r} is not one of {VALID_VERDICTS}"]

    raw_reasons = document.get("reasons")
    reasons = [str(r) for r in raw_reasons] if isinstance(raw_reasons, list) else []

    base_sha = (base_sha or "").strip()
    head_sha = (head_sha or "").strip()

    def bound_sha(field: str) -> str:
        """Read ``<field>.sha`` without trusting the shape of ``<field>``."""
        section = document.get(field)
        if not isinstance(section, dict):
            return ""
        return str(section.get("sha") or "").strip()

    if head_sha:
        bound = bound_sha("candidate")
        if bound != head_sha:
            return UNKNOWN, reasons + [
                f"AER_EVIDENCE_UNBOUND: candidate.sha={bound or '<missing>'} does not match head {head_sha}"
            ]

    if base_sha:
        bound = bound_sha("baseline")
        if bound != base_sha:
            return UNKNOWN, reasons + [
                f"AER_EVIDENCE_UNBOUND: baseline.sha={bound or '<missing>'} does not match base {base_sha}"
            ]

    return str(verdict), reasons


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", required=True, help="Path to the evidence JSON")
    parser.add_argument("--exit-code", type=int, required=True, help="Exit code of `aer accept`")
    parser.add_argument(
        "--schema-exit-code",
        type=int,
        default=0,
        help="Exit code of an optional structural validation (aer verify-evidence)",
    )
    parser.add_argument("--base-sha", default="", help="Base commit under review")
    parser.add_argument("--head-sha", default="", help="Candidate commit under review")
    parser.add_argument("--github-output", default="", help="Path to $GITHUB_OUTPUT")
    args = parser.parse_args(argv)

    verdict, reasons = evaluate(
        Path(args.evidence),
        exit_code=args.exit_code,
        base_sha=args.base_sha,
        head_sha=args.head_sha,
        schema_exit_code=args.schema_exit_code,
    )

    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as handle:
            handle.write(f"verdict={verdict}\n")
            handle.write(f"reasons={json.dumps(reasons)}\n")

    print(f"AER Verdict: {verdict} (aer exit: {args.exit_code})")
    for reason in reasons:
        print(f"  - {reason}")

    return EXIT_BY_VERDICT[verdict]


if __name__ == "__main__":
    sys.exit(main())
