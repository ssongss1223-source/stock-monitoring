# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

---

## 0. Plan Storage Rules

- All generated plans must be stored inside:
  ./.claude/plans/

- Never use:
  ~/.claude/plans/

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

---

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself:
"Would a senior engineer say this is overcomplicated?"
If yes, simplify.

---

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test:
Every changed line should trace directly to the user's request.

---

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
...

Strong success criteria let you loop independently.
Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:**
fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

---

## 5. 프로젝트별 규칙 (stock-monitoring)

### VM 권한 규칙

`/opt/stock-monitor/data/` 와 그 안의 파일(특히 `stock.duckdb`)은 **절대 KHSong으로 chown하지 말 것.**
서비스가 `stock` 유저로 실행되므로 소유권이 바뀌면 DB 접근 실패.

- git pull 권한 문제 시: `backtest/`, `scripts/`, `mcp/` 만 chown. `data/`는 절대 포함 금지.
- chown 화이트리스트: `backtest scripts mcp` (data, .venv, agents, core, config 제외)
- DB 읽기/쓰기 스크립트는 반드시 `sudo -u stock`으로 실행

### DuckDB SQL 규칙

DuckDB는 **window function 안에 window function 중첩 불가.** 반드시 CTE를 단계적으로 분리.

- `AVG(AVG(x) OVER ()) OVER ()` → Binder Error
- `SUM(CASE WHEN LAG() OVER () ...) OVER ()` → Binder Error
- 해결: 첫 번째 window → CTE1, 두 번째 window → CTE2로 분리
- bash에서 `grep -c` + `|| echo 0` 조합은 "0\n0" 출력 버그 → `grep ... | wc -l` 사용

### 파이프라인 실행 규칙

- 파이프라인 시작 전 반드시 기존 프로세스 확인: `pgrep -fa "build_historical\|feature_eng\|train_models"`
- VM git pull 전 로컬 편집 있으면: `sudo git reset --hard origin/main`
- 로그 초기화는 `deploy_and_run.sh`가 담당 — 직접 하지 말 것
- 실행 명령: `bash /opt/stock-monitor/scripts/deploy_and_run.sh [--skip-relabel] [--skip-build]`