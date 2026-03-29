## Skills

A skill is a set of local instructions stored in a `SKILL.md` file. Below is the list of skills that can be used in this project.

### Available skills

- skill-creator: Guide for creating effective skills. Use when creating a new skill or updating an existing skill. (file: /mnt/c/Users/ASUS/.codex/skills/.system/skill-creator/SKILL.md)
- skill-installer: Install Codex skills into `$CODEX_HOME/skills` from a curated list or a GitHub repo path. (file: /mnt/c/Users/ASUS/.codex/skills/.system/skill-installer/SKILL.md)
- doc-write: Create a new Tech Spec and Plan pair for one component using `docs/system-design.md` as the source of truth and leave `[USER_INPUT_REQUIRED: ...]` for missing decisions. (file: /mnt/c/Users/ASUS/.codex/skills/doc-write/SKILL.md)
- doc-revise: Revise existing Tech Spec or Plan documents using `docs/system-design.md` as the primary source of truth, check related component specs for contradictions, stop for missing decisions, and run a mandatory review plus second revision pass before completion. (file: /mnt/c/Users/ASUS/.codex/skills/doc-revise/SKILL.md)
- spec-task-impl: Implement one bounded coding task from an existing component Spec and Plan by grounding on current contracts, limiting scope, writing tests, and reporting blockers instead of inventing missing behavior. (file: /home/artyom9/.codex/skills/spec-task-impl/SKILL.md)

### How to use skills

- Discovery: The list above is the skills available in this project. Skill bodies live on disk at the listed paths.
- Trigger rules: If the user names a skill with `$SkillName` or plain text, or if the task clearly matches a listed skill description, use that skill for the turn.
- Progressive disclosure:
  1. Open the skill's `SKILL.md`.
  2. Read only enough to follow the workflow.
  3. Load files under `references/` only when needed.
  4. Prefer bundled scripts, assets, and templates when they exist.
- Safety: If a named skill cannot be read or applied cleanly, state the issue briefly and continue with the best fallback.

## Project Rules

- Before adding new logic, check whether the same behavior already exists elsewhere and extend or reuse it instead of duplicating it.
- Do not leave the same responsibility implemented in two places unless the duplication is temporary and explicitly documented.

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

