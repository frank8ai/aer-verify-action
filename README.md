# AER Verify Action

Thin wrapper around the compiled [AER Verify](https://github.com/frank8ai/aer-runtime)
standalone runtime. It downloads a pinned release binary, verifies its checksum, runs
`aer accept` against the pull request, and turns the result into a check status.

## Usage

```yaml
name: AER Verify
on:
  pull_request:
    branches: [main]

permissions:
  contents: read

jobs:
  verify:
    name: AER Verify
    runs-on: ubuntu-latest
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: frank8ai/aer-verify-action@main
        with:
          # Required. Must be a release tag that actually has assets attached.
          version: v0.2.0
          docker-image: python:3.12-slim
```

`version` has **no default on purpose**. An earlier revision defaulted to
`0.1.0-beta.2`, whose release assets do not exist, so every consumer failed at the
download step with a 404 that looked like a network problem.

## What the result means

| Status | Meaning |
| :--- | :--- |
| green (exit 0) | `aer accept` exited 0 **and** the evidence document binds to this exact base/candidate pair |
| red, exit 1 | the candidate was executed and a defect was reproduced |
| red, exit 2 | inconclusive: the run crashed, produced nothing, failed schema validation, or the evidence belongs to different commits. A human decides |

Exit 2 is not a soft failure. Anything that cannot be positively established is
`UNKNOWN`, and `UNKNOWN` is non-success.

## Verifying the binary

The install step checks `SHA256SUMS`. That proves the bytes arrived intact — it does
**not** prove we built them, because whoever can rewrite the release asset can rewrite
the checksum next to it. Authenticity comes from the build provenance attestation on
the same release:

```bash
gh attestation verify /path/to/aer --repo frank8ai/aer-runtime
```

An earlier revision of this action downloaded `aer.sigstore.json` and asserted that its
`signedDigest` equalled `sha256(aer)`. That assertion was a tautology — the file was
produced by hashing the same binary and carried no signature, certificate or
transparency-log entry. It has been removed rather than kept as a warning, because a
green "Verified Sigstore bundle" line that verifies nothing is worse than no line.

## Development

`validate_evidence.py` and `tests/test_evidence_admissibility.py` are mirrored from
`frank8ai/aer-runtime` (`action/validate_evidence.py`,
`tests/contracts/test_evidence_admissibility.py`). Keep them identical; the only local
edits are the `ACTION_DIR` path and the `aer accept` invocation literal.

```bash
python -m unittest discover -s tests -p "test_*.py"
```
