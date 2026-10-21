# ADR 0003: Activation uses effective strength; time units; downscale calibration

Status: Proposed (architect), needs owner nod on item 4 | Resolves S G2, G11, G12

**Context.** T§3 defines `B = ln(n/(1-d)) - d*ln(age)` with `n` = access count, yet T§7.3 decays `strength` and archives on activation. Decaying strength cannot move an access-count-based activation, so nothing would ever be archived by downscale, defeating the design.

**Decision.**
1. `n_eff = strength` (float, floor 1e-3). `reinforce()` increments `strength` (per T§5/T§6 amounts) and `access_count` (integer, kept for prune rule, export confidence floor, and the "zero access" test). New memory starts at strength 1.0.
2. `age` is in **days**, floored at 1 minute (1/1440 day). G11 suggested a 1 s floor; with seconds, an episodic memory is below -3.2 within ~50 minutes and everything would archive at once. With days, an unreinforced episodic memory (d=0.5, salience 0.5) archives around day 30 and a semantic one (d=0.2) around day 40-45, assuming 1 downscale period = 1 day and factor 0.93.
3. All time comes from an injectable clock (`now()` parameter / `Cortex.clock`); tests simulate days.
4. **Recommended (owner decides):** decay is applied twice (strength shrink AND `-d*ln(age)`), and downscale is kind-agnostic, so semantic/procedural facts fade almost as fast as episodes. Recommend per-kind downscale periods scaled by `d_kind/0.5` (episodic 1.0, semantic 0.4, procedural 0.2). Until approved, ship the owner's uniform 0.93 behind a `DOWNSCALE_RATE` table so the change is one constant; T05 produces a half-life table for the owner.

**Alternatives rejected.** Keep `n = access_count` and archive on strength (contradicts T§7 "archive by activation"); apply decay only via `age` (then downscale is pointless).
