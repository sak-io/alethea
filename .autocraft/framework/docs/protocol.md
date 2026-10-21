# Collaboration protocol

The rules that let separate agents work as one team. Every agent definition follows this file.

## 1. State lives in the repo
| What | Where |
|---|---|
| Specs | `docs/specs/*.md` |
| Architecture + status | `docs/architecture/` (`status.md`: `DRAFT / IN REVIEW (round N) / APPROVED`) |
| Architecture reviews | `docs/architecture/reviews/review-N.md` |
| Decisions | `docs/adr/NNNN-*.md` |
| Onboarding evidence | `docs/onboarding-report.md` |
| Live status board | `docs/STATUS.md` |
| Code review state | PR comments + `agent:*` labels |
| Knobs | `.autocraft/config.yml` |

## 2. Handoff block
Every agent ends its work with:

```
HANDOFF
From: <agent>            To: <agent | human>
Status: DONE | NEEDS-CHANGES | BLOCKED | NEEDS-HUMAN
Summary: <2-4 lines>
Artifacts: <paths / PR links>
Open items: <numbered list, or "none">
Next: <what the receiver should do>
```

## 3. Review rules (design reviews and PR reviews)
**Severity prefixes**

| Tag | Meaning | Blocks approval? |
|---|---|---|
| `[BLOCKER]` | wrong, insecure, or breaks things | yes |
| `[MAJOR]` | significant quality, test or design gap | yes |
| `[MINOR]` | should fix; may be deferred with a tracked issue | no (if tracked) |
| `[NIT]` | optional taste | no |
| `[QUESTION]` | needs an answer before judging | until answered |
| `[RELEASE-BLOCKER]` | (release-gatekeeper) would hurt in production | yes |

**Author duties:** answer *every* comment (`Fixed in <sha>` / `Not changing because …`); never resolve the reviewer's threads; re-run tests; re-request review.
**Reviewer duties:** re-verify *each* earlier comment on every round; approve only with zero open BLOCKER/MAJOR and green CI on the latest commit; accept sound pushback; stay specific and kind.
**Round limit:** `max_review_rounds` (default 3). Beyond it: label `needs-human`, summarise both positions neutrally, wait for the human.

## 4. PR label state machine
```
agent:ready ─► agent:in-progress ─► agent:needs-review ─► agent:approved ─► agent:release-ready ─► (human merges)
                                         ▲    │                 │
                                         │    ▼                 │ (gaps found)
                                    agent:changes-requested ◄───┘
anything stuck ─► needs-human
```
Exactly one `agent:*` state label on a PR at a time. Whoever moves the PR forward swaps the label.

## 5. Who may do what
| Action | Agents | Human |
|---|---|---|
| Commit to feature branches, open PRs | ✅ developer | ✅ |
| Review, label, comment | ✅ reviewer, gatekeeper | ✅ |
| Approve architecture | ✅ principal-engineer (+ human at L1/L2) | ✅ |
| **Approve & merge PRs into `main`** | ❌ never | ✅ always (in `session-branch` mode: the owner merges the session branch's pull request; inside the session branch the team lead integrates reviewer-approved task branches) |
| Change branch protection / visibility / delete repo | ❌ never without explicit ask | ✅ |
| Create the repo | ✅ after human confirmation | ✅ |

Branch protection enforces the human gate: ≥1 approving review is required, and agents run with your credentials, so the *approval click is yours*.

## 6. Autonomy levels (`.autocraft/config.yml → autonomy_level`)
| Level | You approve | Best for |
|---|---|---|
| **L1 Guided** | every phase boundary (spec, architecture, onboarding, each PR before review) | learning the process |
| **L2 Balanced** (default) | architecture, and every merge | most projects |
| **L3 Hands-off** | merges/releases only | trusted, low-risk work |

Agents never go below "human merges", at any level.

## 7. Definitions
**Definition of Ready (task):** clear acceptance criteria · dependencies merged · test expectations stated · small enough for one PR.
**Definition of Done (PR):** acceptance criteria met · unit + integration tests added and passing · CI green on latest commit · reviewer `VERDICT: APPROVED` · pre-release check passed · docs/changelog updated · human merged.

## 8. Parallelism
Run multiple developers only on independent tasks (see `Depends on` in the work breakdown) touching different files, each in its own branch/worktree. Cap: `max_parallel_developers`.

## 9. Safety rules for all agents
No secrets in code, logs or comments · never disable tests/checks/protections to get green · ask before destructive or outward-facing actions · be honest about what was verified vs assumed.

## 10. Audience adaptation (`experience`)
`.autocraft/config.yml → experience` tells every agent how to talk to the human; it never changes the quality gates.

| Level | Agents… |
|---|---|
| **beginner** (default) | explain jargon inline; lead with "what this means for you / do you need to act"; recommend defaults instead of asking for technical choices; record choices as ADRs; point to `/explain` and `docs/glossary.md`; ask few, concrete, yes/default questions |
| **intermediate** | normal terminology, short rationale, options when they matter |
| **expert** | terse; trade-offs and alternatives; ask only for genuine decisions |

Constants at every level: tests, security checks, independent review, and **a human merges**.

## 11. Capabilities
`capabilities` lists cross-cutting needs (authentication, payments, database, …). Each has a profile in `capabilities/` with *Safe defaults*, *Must test*, *Common mistakes*, and what *not* to build yourself. Architects design them in, developers implement and test them, reviewers/gatekeepers/auditors check them.

## 12. Collaboration surface (GitHub or not)
The protocol works with any surface:
- **GitHub (preferred):** pull requests, review comments, `agent:*` labels (sections 3-4).
- **No GitHub / no remote:** use files. For task `T3`, keep `.autocraft/work/T3/` containing `handoff-N.md` (the handoff block), `review-N.md` (reviewer findings with the same severity tags and a `VERDICT:` line), and `response-N.md` (author's answers: `Fixed in <commit>` / `Not changing because …`). State lives in the files, so any tool or session can continue. The human "merge" is then the human's own commit/merge of the branch.
- **Tools without sub-agents:** run roles as hats, one role per session; reviewers always in a **fresh session** that sees only the spec, the diff and the protocol.

## 13. GitHub operations: `gh` or the session's GitHub tools
Use `gh` if it is installed. Cloud sessions (Claude app, Claude Code on the web) usually have no `gh`; they have GitHub tools instead. Same operations, different handles:

| Operation | `gh` | GitHub tools (names as in Claude cloud sessions) |
|---|---|---|
| Read a PR: details, diff, changed files, check status | `gh pr view / diff / checks` | `pull_request_read` (choose the method for details, diff, files or status) |
| Post a review with line comments | `gh pr review`, `gh api` | `pull_request_review_write` (create pending) -> `add_comment_to_pending_review` -> `pull_request_review_write` (submit) |
| Reply on a review thread | `gh api` | `add_reply_to_pull_request_comment` |
| Comment on a PR / issue | `gh pr comment` | `add_issue_comment` |
| Add/remove a label, edit an issue | `gh pr edit --add-label`, `gh issue edit` | `issue_write` (a PR is an issue for labels) |
| Create a PR (only when the owner asked in `session-branch` mode) | `gh pr create` | `create_pull_request` |
| CI results and failing logs | `gh run view`, `gh pr checks` | `pull_request_read` (status), `actions_get`, `get_job_logs` |
| Read a file from another repo/branch | `gh api` | `get_file_contents` |
| Create a repository (only after the owner confirms) | `gh repo create` | `create_repository` |
| **Merge a PR** | never | **never** |

If a needed operation has no tool in your session, say so plainly and fall back to files (section 12); do not improvise with credentials.

## 14. Git modes (`git_mode` in `.autocraft/config.yml`)
- **`session-branch` (default):** everything happens on the branch the session started on. Parallel work uses *local* git worktrees (`feat/<task>`, never pushed) that the team lead merges into the session branch (`--no-ff`) once the reviewer approved. Handoffs and reviews are files in `.autocraft/work/<task>/` committed on the session branch. No extra remote branches, no pull requests unless the owner asks; push the session branch regularly so work survives the session. The owner's merge gate is the pull request from the session branch into `main`.
- **`pr-per-task`:** one pushed branch and pull request per task with `agent:*` labels (sections 3-4); the owner merges each.
