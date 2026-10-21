# ADR 0007: Order and case vetoes in similarity

Status: accepted (T12 review)

## Context
End-to-end encode() found two silent false merges at similarity 1.0: role swaps ("Alice reports to Bob" / "Bob reports to Alice", identical bags of words) and case-only identifier differences (`getUserName` / `getUsername`, invisible because tokens were keyed by lowercase).

## Decision
- Order veto: when the stemmed token multisets overlap >= 0.95 but the ordered sequences differ, sim *= 0.55. Like the substitution damp it never stacks on top of the identifier or negation veto.
- Identifier veto also compares raw-case tokens present on both sides: a differing camelCase or underscore token (case change or inserted capital) vetoes.

## Consequences
A pure reordering restatement ("X then Y" vs "Y then X") no longer merges. This is the intended bias to false negatives; reinforcement self-corrects redundant traces.
