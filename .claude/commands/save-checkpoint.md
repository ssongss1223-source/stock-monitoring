# 체크포인트 저장

현재 작업 상태를 검토하고, `docs/checkpoint.md`를 **최신 상태 스냅샷**으로 갱신한다.

## 목적

`docs/checkpoint.md`는 작업 재개를 위한 **짧고 읽기 쉬운 최신 상태 문서**로 유지한다.

이 파일은 긴 작업 로그가 아니라, 다음 세션에서 빠르게 맥락을 복원하기 위한 문서다.

상세 이력이나 세션별 기록이 필요하면 `docs/work-log.md`에 별도로 누적한다.

## 기본 원칙
- `docs/checkpoint.md` = 최신 상태 요약본
- `docs/work-log-YYYY-MM.md` = 월별 누적 작업 로그 (예: `docs/work-log-2026-05.md`)

## 할 일
1. 현재 작업 맥락과 최근 변경 내용을 검토한다.
2. `docs/checkpoint.md`를 최신 상태 기준으로 갱신한다.
3. 나중에 작업을 다시 시작할 때 꼭 필요한 정보만 남긴다.
4. 오래됐거나 중복되거나 오해를 부를 수 있는 항목은 정리한다.
5. 필요하면 `docs/work-log.md`에 이번 세션의 짧은 로그를 추가한다.

## `docs/checkpoint.md` 필수 구조

# Checkpoint

## Current Goal
- 지금 가장 중요한 목표 1개

## Current Status
- 현재 상태 요약

## Done
- 최근 완료한 핵심 작업
- 최근 완료한 핵심 작업

## Remaining
- 아직 남아 있는 핵심 작업
- 아직 남아 있는 핵심 작업

## Risks / Blockers
- 현재 리스크, 막힘, 불확실성
- 필요한 가정이 있으면 함께 기록

## Next Actions
1. 가장 좋은 다음 액션
2. 그 다음 액션
3. 필요 시 세 번째 액션

## References
- 중요한 파일: `path/to/file`
- 중요한 문서: `docs/...`

## Last Updated
- YYYY-MM-DD HH:mm

## `docs/checkpoint.md` 작성 규칙
- 짧게 유지한다.
- 긴 회고나 장문 설명 대신 현재 상태 중심으로 정리한다.
- 상세 이력은 여기에 길게 남기지 않는다.
- 이미 해결되어 더 이상 중요하지 않은 내용은 제거한다.
- 한 번 훑어서 바로 이해할 수 있는 형태를 유지한다.
- 파일이 없으면 위 구조로 새로 만든다.
- **Done 섹션은 최대 5개**만 유지한다. 초과 항목은 삭제하거나 work-log에 기록 후 제거한다.

## `docs/work-log-YYYY-MM.md` 규칙
- 현재 달의 파일에 새 세션을 **맨 위**에 추가한다 (최신이 위).
- 파일이 없으면 `docs/work-log-YYYY-MM.md`로 새로 만들고 `docs/work-log.md` 인덱스에 링크를 추가한다.
- 꼭 필요한 경우에만 짧게 append 한다.
- 길고 장황한 일지는 피하고, 실제로 도움이 되는 정보만 남긴다.

## `docs/work-log-YYYY-MM.md` 권장 형식

## YYYY-MM-DD 세션 N — 한 줄 요약
- 작업:
- 변경 사항:
- 관련 파일:
- 메모:
- 다음 아이디어: