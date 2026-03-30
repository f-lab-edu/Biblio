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
- When a test file covers multiple scenario groups, organize related tests into meaningful pytest classes (e.g., `TestSearchSuccess`, `TestSearchValidation`) to separate success, validation, and error scenarios.
- Do not apply classes mechanically to small, single-purpose test files; use them only when grouping genuinely improves readability.

## Docs
- Update Specs/Plans only when code behavior or contract actually changes.
- Keep documentation concrete: state inputs, outputs, status transitions, and failure rules.

## Review Focus
- Prioritize correctness, state transitions, retry/failure handling, and data consistency.
- Call out drift between code and docs explicitly.
- Prefer simple explanations over long theory when answering review comments.

## Python / FastAPI / SonarCloud Rules

When writing or modifying Python code, proactively avoid patterns that fail SonarCloud quality gates.

### FastAPI
- Use `Annotated[...]` for dependency injection parameters instead of `param: Type = Depends(...)`.
- Do not specify `response_model=...` when it duplicates the function return type annotation.
- Prefer framework-idiomatic FastAPI signatures and avoid legacy patterns unless required by existing code.

### Async
- Do not mark a function `async` unless it actually uses `await` or must satisfy an async interface.
- This applies especially to nested test handlers, mock callbacks, and test doubles.
- If a test double must remain async for interface compatibility, make that explicit in the code.

### Tests
- Do not compare floating point values with direct equality; use `pytest.approx(...)`.
- Prefer `https://` over `http://` in test and example URLs unless plain HTTP is explicitly required.

### Readability / Complexity
- Keep functions small and focused.
- Split functions that mix I/O, validation, retry handling, and exception translation into helpers.
- Treat high cognitive complexity as a design problem, not something to suppress or ignore.

### Naming
- Use standard snake_case for local variables and helper names.
- Avoid capitalized local variable names unless they are actual class/type definitions.

### Cross-service Duplication
- Do not copy shared utility or middleware code between services without explicit approval.
- Before duplicating logic, check whether it should be extracted to a shared module.
- If duplication is temporarily unavoidable, call it out explicitly.

### Before Finishing
- Review changed files for likely SonarCloud issues before concluding work.
- Check for: duplicate FastAPI dependency signatures, unnecessary `async`, float equality in tests, insecure test URLs, large copy-pasted blocks, overly complex exception-handling functions.
