# epaper-conductor — team workflow

The main session is the **PM** (run it on Fable). The PM plans, splits the work, hands each piece to a subagent in `.claude/agents/`, and owns every commit, push and decision that reaches the user or the units.

| Agent | Model | Does |
|---|---|---|
| `implementer` | opus | one scoped code change plus its tests |
| `tester` | sonnet | runs pytest, separates regressions from known environment errors |
| `reviewer` | opus | read-only review of the diff before commit |
| `doc-writer` | sonnet | STATUS / SPECIFICATION / guides after a change lands |
| `Explore` (built-in) | — | wide read-only searches across the 150k-line tree |

## How the PM runs a task
1. Read `docs/STATUS.md` (what to do next) and pin the goal and the acceptance check.
2. Use `Explore` when the files in play are not yet known; skip it when they are.
3. Hand independent pieces to several `implementer`s in one message so they run in parallel; give each its own files so they do not collide.
4. `tester` on the touched areas, `reviewer` on the diff — in parallel. Send findings back to the same implementer with SendMessage.
5. Commit, then `doc-writer` for the docs, then push when the user asks.

Small, one-file fixes the PM can do directly; the team is for work that splits.

## Rules for everyone
- Never `git stash` or reset the working tree: the operator's running Conductor serves `conductor/web/` from it.
- No SSH, OTA or firmware actions on the units without the user's go-ahead in chat.
- The units still use `~/E-paper_H_WALL_BRICKS_Raspi` (repo renamed 2026-10-14, Radxa migration deferred — `docs/STATUS.md` §4).
- `H_WALL_BRICKS` is the panel name; keep it.
