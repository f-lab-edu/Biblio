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
