# ADR 0006: Substitution damp in similarity

Status: accepted (T07)

## Context
Long sentences with one swapped entity (staging/prod, alice/bob) keep raw max(cosine, jaccard) >= 0.88, and neither the identifier nor the negation veto fires. A false merge is silent and permanent.

## Decision
Add a third damp (sim *= 0.55) when both texts hold stemmed tokens the other lacks (a swap, not a pure addition). It applies only if neither the identifier nor the negation veto fired, so damps never add a third factor to an already-vetoed pair.

## Consequences
Deliberate bias toward false negatives; the damp only lowers similarity. Known edges: reworded or synonym restatements, and a dropped article on short sentences, sit at or near the 0.88 edge and will not merge. A redundant trace self-corrects under reinforcement.
