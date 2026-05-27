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

### 로컬 ↔ VM 역할 분리

| 작업 | 위치 |
|------|------|
| 코드 편집 / git commit / push | **로컬** |
| 파이프라인 실행 / DB 조작 / 서비스 재시작 | **VM** |
| VM에서 코드 직접 편집 | **금지** — git pull로만 반영 |

배포 흐름: 로컬 편집 → commit → push → VM `sudo git pull` → `init_db()` (스키마 변경 시) → 서비스 재시작.

### VM 권한 규칙

`/opt/stock-monitor/data/` 와 그 안의 파일(특히 `stock.duckdb`)은 **절대 KHSong으로 chown하지 말 것.**
서비스가 `stock` 유저로 실행되므로 소유권이 바뀌면 DB 접근 실패.

- git pull 권한 문제 시: `backtest/`, `scripts/`, `mcp/` 만 chown. `data/`는 절대 포함 금지.
- chown 화이트리스트: `backtest scripts mcp` (data, .venv, agents, core, config 제외)
- DB 읽기/쓰기 스크립트는 반드시 `sudo -u stock`으로 실행

### MCP SSH 사용 주의사항

`mcp__vm-ssh__ssh_run`은 paramiko 기반 — 다음 제약이 있음:

- **리다이렉트 금지**: `>` / `>>` 포함 명령 → paramiko가 출력을 읽지 못함. 백그라운드 실행은 `screen -dm <cmd>` 사용.
- **비교 연산자 공백 주의**: `value > 1e12` → paramiko가 리다이렉트로 오인. `value>=1e12` (공백 없이) 사용.
- **VM 시간 가정 금지**: 작업 전 `date` 명령으로 실제 VM 시간 확인 (UTC/KST 혼동 방지).

### DuckDB SQL 규칙

DuckDB는 **window function 안에 window function 중첩 불가.** 반드시 CTE를 단계적으로 분리.

- `AVG(AVG(x) OVER ()) OVER ()` → Binder Error
- `SUM(CASE WHEN LAG() OVER () ...) OVER ()` → Binder Error
- 해결: 첫 번째 window → CTE1, 두 번째 window → CTE2로 분리
- bash에서 `grep -c` + `|| echo 0` 조합은 "0\n0" 출력 버그 → `grep ... | wc -l` 사용
- **INSERT 컬럼 수 불일치 방지**: `INSERT OR REPLACE INTO table SELECT ...` 시 반드시 명시적 컬럼 리스트 사용. 위치 기반(positional) INSERT는 BinderException 유발.

### 파이프라인 실행 규칙

- 파이프라인 시작 전 반드시 기존 프로세스 확인: `pgrep -fa "build_historical\|feature_eng\|train_models"`
- VM git pull 전 로컬 편집 있으면: `sudo git reset --hard origin/main`
- 로그 초기화는 `deploy_and_run.sh`가 담당 — 직접 하지 말 것
- 실행 명령: `bash /opt/stock-monitor/scripts/deploy_and_run.sh [--skip-relabel] [--skip-build]`

### 수정 후 검증 절차

코드/스키마 변경 후 배포 전에 반드시 확인:

| 변경 종류 | 검증 명령 |
|-----------|-----------|
| Python 파일 수정 | `python3 -c "from module import X; print('OK')"` |
| DB 스키마 변경 (ALTER/ADD) | `sudo -u stock python3 -c "from data.db import init_db; init_db(); print('OK')"` |
| 서비스 재시작 후 | `journalctl -u stock-monitor --since "1 min ago" --no-pager \| tail -10` |
| 배치 완료 후 | `tail -30 /opt/stock-monitor/stock_monitor.log` 로 ERROR/Exception 확인 |

### 데이터 무결성 원칙

피처/라벨/컬럼 추가·변경 시 반드시:

1. `data/db.py` `_MIGRATIONS`에 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 추가
2. `init_db()` VM 실행 (`sudo -u stock`) → 컬럼 수 확인
3. INSERT 문은 명시적 컬럼 리스트 필수 (DuckDB SQL 규칙 참조)

빠른 DB 상태 확인:
```bash
# 테이블별 컬럼 수
sudo -u stock python3 -c "
from data.db import get_conn
conn = get_conn(read_only=True)
for t in ['backtest_labels','universe_daily','universe_features_daily']:
    n = len(conn.execute(f\"SELECT * FROM {t} LIMIT 0\").description)
    print(f'{t}: {n}컬럼')
conn.close()
"
# 최근 수집 행수
sudo -u stock python3 -c "
from data.db import get_conn
conn = get_conn(read_only=True)
print(conn.execute(\"SELECT MAX(date), COUNT(*) FROM universe_daily WHERE date=( SELECT MAX(date) FROM universe_daily)\").fetchone())
conn.close()
"
```

상세 검증 절차: `docs/system.md` 참조.