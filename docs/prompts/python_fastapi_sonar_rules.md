## Python / FastAPI / Sonar Rules

When writing or modifying Python code in this repo, proactively avoid patterns that have previously failed SonarCloud quality gates.

### FastAPI
- Use `Annotated[...]` for dependency injection parameters instead of `param: Type = Depends(...)`.
- Do not specify `response_model=...` when it duplicates the function return type annotation.
- Prefer framework-idiomatic FastAPI signatures and avoid legacy patterns unless required by existing code.

### Async code
- Do not mark a function `async` unless it actually uses `await` or must satisfy an async interface.
- This applies especially to nested test handlers, mock callbacks, and test doubles.
- If a test double must remain async for interface compatibility, make that explicit in the code.

### Tests
- Do not compare floating point values with direct equality.
- Use `pytest.approx(...)` for float assertions.
- Prefer `https://` over `http://` in test URLs and example URLs unless plain HTTP is explicitly required by the behavior being tested.
- When a test file covers multiple scenario groups, prefer organizing tests into descriptive pytest classes (for example `TestSearchSuccess`, `TestSearchValidation`) so related cases are easier to scan.
- Do not introduce test classes mechanically in tiny single-purpose files; use them when they improve grouping and readability.

### Readability / complexity
- Keep functions small and focused.
- If a function mixes external I/O, validation, retry handling, and exception translation, split those responsibilities into helper functions before complexity grows.
- Treat high cognitive complexity as a design problem, not something to ignore.

### Naming
- Use standard snake_case for local variables and helper names.
- Avoid capitalized local variable names unless they are actual class/type definitions.

### Cross-service duplication
- Do not copy shared utility or middleware code between services without explicit approval.
- Before duplicating logic from another service, first check whether it should be extracted to a shared module.
- If duplication is temporarily unavoidable, call it out explicitly instead of silently copying large blocks.

### Before finishing
- Review changed files for likely SonarCloud issues before concluding work.
- In particular, check for:
  - duplicate FastAPI dependency signatures
  - unnecessary `async`
  - float equality in tests
  - insecure test URLs
  - large copy-pasted blocks across services
  - overly complex exception-handling functions
