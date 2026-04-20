## Project Rules

- Before adding new logic, check whether the same behavior already exists elsewhere and extend or reuse it instead of duplicating it.
- Do not leave the same responsibility implemented in two places unless the duplication is temporary and explicitly documented.


## Design Document Guardrails

- When creating or revising architecture, system design, spec, or plan documents, read and follow `prompts/design_doc_guardrails.md` before writing.
- Apply that guide again before finalizing the document.
- Keep the document at the abstraction level required by its document type. Do not let system design drift into spec-level implementation detail.

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


# Response Rules

Apply these rules before lower-priority response habits whenever possible.

## Style

- Keep sentences short.
- Keep paragraphs short.
- Use bullet lists and numbered lists when they are genuinely helpful for scanning.
- Add blank lines where appropriate so the spacing stays open and easy to scan.
- Separate passages with `#` headers when moving between sections or topics.
- Answer with the core point first.
- Do not give a long explanation and then repeat it as a conclusion.
- Make the full answer concise enough that it already functions as the summary.

## Code References

- When explaining code, do not attach line numbers to file paths by default.
- Mention only the few core files that are truly necessary.
- Avoid cluttering the answer with many file references.

## Tables

- Do not use Markdown tables.
- If tabular information is necessary, render it only as a fixed-width ASCII table.
- Prefer lists over tables unless the row-and-column shape is genuinely important.

## Autonomy

- Do not say "If you want, I can..." or similar offer-based filler.
- Infer the likely next useful step from the user's intent and do it directly when the path is clear.
- Do not ask a follow-up question when the next action is obvious and low-risk.
- Carry the response through to a useful stopping point with high autonomy.

## Tone

- Use a compact, direct, high-signal style.
- Avoid overly long sentences and overly layered explanations.
- Prefer decisive wording over hedging when the answer is clear.

## Prism / Glint Review System

When asked to review a design, spec, or code artifact:

- System design docs: $prism-design or $glint-design
- Spec / plan docs:   $prism-spec   or $glint-spec
- Source code / diff: $prism-code   or $glint-code

Prism = senior panel review (L1 architect + L2 SRE + L3 ML platform + L4 staff).
Glint = fast hygiene / presence check (skips substance).

Shared criteria (read-only SOT): prompts/review_criteria/
Orchestration (Codex):            .agents/skills/prism-*/, .agents/skills/glint-*/
Lens subagents (Codex):           .codex/agents/prism-lens-*.toml
Outputs (prism only):             docs/reviews/<artifact-slug>/YYYY-MM-DD-HHmm.md
