# Biblio Working Rules

## Priority
- Follow `docs/system-design.md` first when component docs conflict.
- Prefer existing Specs/Plans over inventing new behavior.
- Keep PR scope small. Separate unrelated docs, infra, and feature work.

## Code Changes
- Match existing folder structure and naming before introducing new patterns.
- Prefer small, local changes over broad refactors unless explicitly requested.
- Do not add new external dependencies without a clear reason.
- Before adding new logic, check whether the same behavior already exists elsewhere and extend/reuse it instead of duplicating it.
- Do not leave the same responsibility implemented in two places unless the duplication is temporary and explicitly documented.
- When behavior is uncertain, leave a short TODO or note instead of guessing.

## Testing
- Add or update tests for behavior changes.
- Run the smallest relevant test first, then broader checks if needed.
- Avoid brittle tests that only mirror implementation details.

## Docs
- Update Specs/Plans only when code behavior or contract actually changes.
- Keep documentation concrete: state inputs, outputs, status transitions, and failure rules.

## Review Focus
- Prioritize correctness, state transitions, retry/failure handling, and data consistency.
- Call out drift between code and docs explicitly.
- Prefer simple explanations over long theory when answering review comments.
