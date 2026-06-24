# Design Document Guardrails

Use this guide when creating or revising architecture, system design, spec, or plan documents in this repository.

The goal is to keep design documents readable, durable, and focused on the right level of abstraction.

## Core Rules

- Match the abstraction level to the document type.
- State the adopted design directly.
- Define each major decision once.
- Explain concepts first and use identifiers only when necessary.
- Prefer wording that remains valid when implementation details change.

## 1. Keep the Right Abstraction Level

### System design

System design must stay one level above implementation.

Focus on:

- component responsibilities
- service or module boundaries
- data flow
- key technical decisions
- operational constraints
- architecture-level tradeoffs

Avoid drifting into:

- class structure
- function-level behavior
- endpoint-by-endpoint execution detail
- low-level exception branches
- query-level logic
- implementation steps that belong in a spec or plan

System design is not a substitute for a spec.

It should not need frequent rewrites when implementation details change.

### Spec and plan

Spec and plan documents may be more concrete.

They should extend the system design, not restate it in full.

## 2. Concept First, Identifier Second

Explain the concept and the reader context first.

Use implementation identifiers only when they are necessary for precision.

Use identifiers only when:

- the exact code identifier matters
- an API field, database column, or enum value must be named
- the original identifier is required to avoid ambiguity

Rules:

- Write the main explanation in concept terms.
- If an identifier is needed, attach it after the concept is already clear.
- Do not repeat the same identifier across multiple paragraphs without new value.
- Do not replace explanation with a list of field names or status names.

Good examples:

- The job lifecycle is tracked with three internal states: `PENDING`, `RUNNING`, and `FAILED`.
- Document visibility is stored in a dedicated database field named `visibility_status`.

Bad examples:

- If `visibility_status` is `PUBLISHED`, downstream processing is enabled and `draft_flag=false` finalizes exposure scope.
- This step checks `job_status`, `sync_status`, and `index_state` before deciding whether processing continues.

The reader should be able to understand the paragraph even without knowing the code identifiers.

Identifiers should support the explanation, not replace it.

## 3. Describe the Adopted Design Directly

When a design changes, describe the adopted design directly.

Do not fill the document with negative comparison language about the previous option.

Avoid patterns like:

- `This is not A. It is B.`
- `Instead of the previous structure, ...`
- `A is no longer used.`

Prefer:

- `The adopted design uses B.`
- `Data storage is handled in layer B.`
- `The primary flow follows path B.`

Mention older decisions only when history is required for migration, compatibility, or risk analysis.

When that history is needed, isolate it in a short dedicated section such as:

- `Migration Notes`
- `Compatibility Constraints`
- `Decision History`

Do not spread past-versus-current wording throughout the whole document.

## 4. Define Once, Then Reference

A design decision should be defined once in the place where readers expect to find it first.

After that, refer back to it briefly and add only the local implication that matters.

Prefer this pattern:

- define the decision once in the primary section
- reference it briefly in later sections
- add only the new impact relevant to that section

Avoid:

- repeating the same decision in multiple sections with small wording changes
- restating the same rationale under every related component
- copying the same constraint into every subsection

If many sections depend on one decision, create one canonical subsection and reference it.

## 5. Prefer Stable Design Wording

Design documents should stay useful even when specs evolve.

Prefer wording built around stable concerns such as:

- responsibility
- ownership
- boundary
- contract
- lifecycle
- source of truth

Do not include fragile implementation detail unless the document type explicitly requires it.

## 6. Final Check

Before finalizing, confirm:

- Is the abstraction level correct for this document type?
- Does the system design avoid spec-level implementation detail?
- Does the paragraph explain concepts before identifiers?
- Are identifiers supporting clarity instead of replacing it?
- Does the document describe the adopted design directly?
- Are negative references to previous options removed unless they are necessary?
- Is each major decision defined once rather than repeated?
- Can repeated paragraphs be reduced to references?
- Will this wording remain valid if implementation details change?

If any answer is no, revise the document before finalizing it.
