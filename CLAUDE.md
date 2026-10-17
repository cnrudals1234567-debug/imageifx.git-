# imageifx

GPT로 만든 1:1 광고 이미지를 네이버 GFA(최우선), 메타, 구글 사이즈로 변환하는 내부용 도구. 로컬 CPU에서 무료로 동작한다.

## 절대 규칙 (어기지 말 것)
1. **글자 픽셀을 다시 그리거나 폰트·내용을 바꾸지 않는다.** 글자는 원본에서 레이어로 추출해 이동·균등 축소만 한다.
2. **배경 확장에 블러·단색 늘리기를 쓰지 않는다.** 위/아래 띠만 비는 평평한 배경은 가장자리 색 연장, 그 외는 LaMa.
3. **규정 hard FAIL이 있으면 최종본이 아니다.** 안전영역, 용량, 크기, 흰색 면적, 배경색, 가장자리 채움, 글자 잘림은 hard이다.
4. **기준(baseline) 디자인은 사용자 승인 없이 바꾸지 않는다.** 두 기준(테솔, 한국어학과)은 사용자가 "이대로"라고 확정했다. 코드나 규칙을 고친 뒤 기준과 달라지면 의도한 변경인지 먼저 사용자에게 묻는다.
5. 규칙 신뢰도(A/B/C)를 섞어 말하지 않는다. GFA 안전영역·흰색·배경색 규칙은 단일 출처(B)이고 광고관리자 확인 전에는 "확정"이라 하지 않는다. "실제 네이버 검수 통과"를 보장한다고 말하지 않는다.
6. 이미지는 결과를 **직접 열어서** 확인한다. report.json의 PASS만 믿지 않는다(이번 개발에서 코드 검사가 놓친 결함이 눈으로 여러 번 발견됨).

## 환경
- Windows, NVIDIA GPU 없음(CPU 전용). Python 3.12 고정(`uv`), 실행은 `python -m uv run python -m imageifx ...`
- 한글 출력은 `PYTHONIOENCODING=utf-8`. 긴 python 스크립트는 heredoc 대신 파일로 저장해 실행한다(셸 파싱 오류가 났음).
- LaMa 분해(`run`)는 소재당 수 분 걸린다. 좌표만 바꿀 때는 `render`(1초).

## 명령
- `run <이미지> <분석json> --preset gfa_1250x560` 분해부터 전체 실행
- `relayout <작업폴더> --tag X --preset P` 분해 캐시를 재사용해 새 배치
- `render <작업폴더>` layout.json 수정 후 재렌더
- `verify <최종파일> --preset P` 저장된 파일만으로 독립 검증

## 구조
- `imageifx/decompose.py` 글자 영역 자동 보정, 클린 배경, 레이어 추출
- `imageifx/layout.py` GFA 배치(안전영역, 정렬 정규화, 장식 재배치), 세로/정사각 배치
- `imageifx/compose.py` 합성, 배경 확장, 스크림
- `imageifx/validate.py` 규정·글자 무결성·요소 보존·독립 검증
- `presets/presets.json` 매체 규격과 규칙, `docs/gfa_guide.md` 규칙 근거와 신뢰도
- `baselines/` 기준 디자인(최종 jpg, layout, 분해 캐시), `.claude/agents/` 검토 에이전트

## 소재 한 장을 끝내는 순서 (검토 게이트)
analyzer(요소 분석) -> 실행 -> 필요 시 element-fixer -> naturalness-reviewer(글자 잘림·쏠림·정렬 눈 점검)
-> gfa-guide-fitter(마지막 수정 후 서로 다른 3회 검토) -> design-director(PASS/REWORK).
수정하면 검토를 처음부터 다시 한다.

## 이번 합의 (사용자 확정)
- 올빼미 얼굴이 좌측 240px 경계에 걸리는 것, 한국어학과에서 스크림이 노트 손글씨를 덮는 것은 "이대로" 수락됨.
- 우선순위: 디자인 스타일 일관성과 GFA 규정 준수 둘 다 매우 중요.
