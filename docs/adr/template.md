# [Number]. [Title]

**Status:** [Proposed | Accepted | Deprecated | Superseded by [ADR-XXXX](XXXX-title.md)]
**Date:** YYYY-MM-DD
**Deciders:** [List of people involved in the decision]
**Technical Story:** [Optional: Link to PRD / issue / PR]

---

## Context

[What is going on? Why does a decision need to be made now?]

### Background

[Optional: prior art, existing docs, relevant history. Link rather than repeat.]

### Problem Statement

[Clear single-sentence statement of the problem this ADR resolves.]

### Goals

- Goal 1 (measurable if possible)
- Goal 2

### Non-Goals

- Non-goal 1
- Non-goal 2

---

## Decision Drivers

[What factors shape the choice?]

- Driver 1 (e.g., correctness, speed, reversibility)
- Driver 2
- Driver 3

---

## Considered Options

### Option 1: [Name]

**Description:** [Brief description of the approach]

**Pros:**
- Advantage 1 (specific, not "simple")
- Advantage 2

**Cons:**
- Disadvantage 1 (specific)
- Disadvantage 2

### Option 2: [Name]

**Description:** [Brief description]

**Pros:**
- Advantage 1
- Advantage 2

**Cons:**
- Disadvantage 1
- Disadvantage 2

### Option 3: [Name]

(Include as many options as you genuinely considered. Write each neutrally; no sarcasm in the pros/cons.)

---

## Decision

**Chosen Option:** Option [X] — [Name]

### Rationale

[Why this option? Reference specific goals and drivers. Cite the alternatives' key weaknesses, not just that they were rejected.]

---

## Consequences

### Positive

- Consequence 1
- Consequence 2

### Negative

- Consequence 1 (with a mitigation if possible)
- Consequence 2

### Neutral

- Change 1
- Change 2

### Security Considerations

[Explicit security implications, or "N/A — see ADR-XXXX" / "N/A — this decision has no security surface". Do not skip this stanza.]

---

## Implementation

[High-level implementation notes. Name concrete tools (e.g. `ruff` rule, `conftest.py` check) rather than abstract "lint / test".]

### Migration Path

[If applicable, how do we transition from the old approach?]

### Timeline

[Optional: target phases if relevant to the decision.]

---

## Validation

[How do we know the decision was correct?]

### Success Metrics

- Metric 1 (concrete, measurable, with a target)
- Metric 2

### Monitoring

[How do we observe regressions of this decision?]

---

## Related Decisions

[Links to related ADRs.]

- [ADR-XXXX](XXXX-title.md) — [Relationship, e.g. "depends on", "supersedes"]

---

## References

- [Resource 1](url)
- [Resource 2](url)

---

## Notes

[Any additional notes, open follow-ups, or context future readers may need. Deferred decisions in this section MUST name an owner.]

---

## Style rules for this template

1. **UK English throughout** (colour, analyse, behaviour).
2. **Banned words:** "leverage" (as a verb), "seamless", "robust" (as vague praise), "game-changing", "transformative", "well-understood", "easy to X" without specifics, single-word "Simplest" / "Simpler" as a Pro.
3. **No em dashes in code comments or docstrings.** Em dashes in prose are allowed.
4. **Deferred decisions name an owner.**
5. **Banned traceability patterns:** "covered elsewhere" without a link; "required by other ADRs" without naming them; floating links to directories (use file paths).
6. **Security Considerations is required**, not optional. Default is "N/A — see ADR-XXXX"; silence means undecided, not safe.
7. **Once Accepted, content changes require a superseding ADR.** The `Last Updated` metadata records the original write date; use a new ADR with `Supersedes: ADR-NNNN` for material changes.
