# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

## 테스트
- 전체: `python -m uv run pytest -q` (55개, 약 6초, LaMa·토큰 불필요). 단일: `python -m uv run pytest tests/test_validate.py::이름 -q`
- 시스템 `python`은 3.14라 의존성이 없다. 반드시 `python -m uv run ...`을 쓴다. json은 항상 `encoding="utf-8"`로 연다(기본 cp949로 열면 `presets.json`에서 깨짐).
- 단위 테스트는 기준 디자인의 시각 품질을 보장하지 않는다. 코드·상수를 고친 뒤에는 `baselines/_md5_before_refactor.txt`의 md5와 `relayout`/`render` 결과를 비교해 기준이 바뀌었는지 확인한다(규칙 4).

## 웹 UI (`web/`)
- 실행: `python -m uv run uvicorn web.app:app --reload` (사내 공유: `--host 0.0.0.0 --port 8000`). 환경변수: `IMAGEIFX_WEB_PASSWORD`(공용 비밀번호), `IMAGEIFX_DAILY_TOKEN_LIMIT`, `IMAGEIFX_FIX_LIMIT_PER_SIZE`(AI 수정 사이즈당 횟수, 기본 3), `IMAGEIFX_ANALYZE_TOKEN_CAP`/`IMAGEIFX_FIX_TOKEN_CAP`(호출당 상한), `IMAGEIFX_MODEL`(기본 sonnet), `IMAGEIFX_EFFORT`(기본 low).
- 흐름: 업로드(GFA/메타/구글 사이즈 선택) → 분석(`claude -p` 가벼운 호출, 이미지 sha256 캐시) → `imageifx run`(첫 사이즈, LaMa) + `relayout`(나머지) → 사이즈별 페이지(`/jobs/<id>/size/<preset>`)에서 수정. 단일 워커 스레드가 한 번에 하나만 처리한다.
- **수정은 무료 단계 먼저**: 빠른 조정(`ops.QUICK`, 토큰 0) → 자동 보정(`autotune.solve`, 토큰 0) → AI 수정(`fixer.evidence` 한 장 + 연산 JSON 답변, 토큰). 수정은 항상 복사한 새 폴더(`<job>_<preset>_vN`)에서 하고 결과 포인터(`job.results`)만 바꾸므로 되돌리기는 포인터 복원이다. 에이전트는 `Read`만 쓰고 파일을 고치지 않는다.
- 가벼운 호출(`agent.build_cmd(lean=True)`): 작업 폴더를 `output/_jobs/<id>/agent/`로 해서 저장소 CLAUDE.md·메모리가 안 실리고, `--system-prompt`(`web/prompts/`), `--strict-mcp-config`, `--no-session-persistence` 등을 쓴다. `--bare`는 구독 로그인이 안 되어 쓰지 않는다. 호출당 토큰 상한을 넘으면 중단하고 사용량만 기록한다.
- 작업 상태 `output/_jobs/<id>/job.json`, 토큰 기록 `output/_usage.jsonl`. 에이전트를 새로 붙일 때는 `worker._call`로 호출해 사용량이 기록·표시되게 한다.
- 웹은 예시 소재(테솔 등)에 의존하지 않는다. 사이즈 규격·신뢰도는 `presets/presets.json`의 `group/label/grade/verified/note/hidden`과 `docs/size_catalog.md`가 단일 출처다.
- 테스트는 `claude`와 LaMa를 가짜로 바꾼다(`tests/test_web.py`). 실제 에이전트 호출은 토큰을 쓴다.

## 데이터 흐름
`run`: 원본+분석json → `decompose.run`(글자·요소 레이어 PNG + `clean_bg.png`, LaMa) → `layout.build`(스타일 프로필+프리셋으로 `layout.json` 생성) → `cli._finish`(`compose.build_bg` 배경 확장 → `compose.render` → `_export` PNG, 용량 초과 시 JPG 품질을 낮춤 → `validate.*` → `report.json`, `debug.png`, `compare.png`).
- 작업 폴더는 `output/<이름>/`(gitignore). `relayout`은 `clean_bg.png`·`layer_*.png`·`decompose_meta.json`을 새 폴더로 복사해 분해를 건너뛴다. `render`는 `layout.json`을 직접 읽어 다시 그린다.
- 상수는 두 곳이 단일 출처다. 디자인 결과에 영향 주는 값은 `presets/design_spec.json`(코드에서 `spec.get("a.b")`), 매체 규격·규칙은 `presets/presets.json`, 스타일별 배치 옵션은 `presets/styles/*.json`. 코드에 매직넘버를 새로 넣지 말고 여기에 둔다.
- `output/`의 `.log`·임시 폴더는 개발 중 남은 것이며 기준이 아니다. 기준은 `baselines/`뿐이다.

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

## 스타일과 요소 속성 (분석 json 작성 규칙)
- 분석 json 최상단 `"style"`: `dark_board`(어두운 칠판) / `photo_collage`(밝은 사진·콜라주) / `bold_solid`(단색 배경 + 굵은 큰 글씨). 배치 옵션은 스타일이 정하고, CLI 옵션은 덮어쓰기 전용(기록·경고됨).
- 요소 `importance`: `critical`(글자·인물, 안전영역 규칙 대상) / `movable`(재배치할 장식, 배경에서 제거) / `decor`(장식을 부품으로 복사, flat 스타일 전용) / `band`(전체 폭 띠) / `drop`(지우기만).
- `decor` 속성: `anchor`(corner_tl/tr/bl/br, 바닥 기준 비율 유지) · `priority`(keep/optional) · `overlap`(none/subject/any: 글자와 겹쳐도 되는지) · `z`(back/behind_subject/front) · `polarity`(light/dark/any: 배경보다 밝은/어두운 것만 복사) · `fade`(경계를 부드럽게, 원본 px).
- 글자 `group`: `footer`는 띠 안에 따로 배치. 글자 `mask`: `glyph`(글자 모양대로 지움, 단색 배경용) / `bbox`.
- 인물 `text_overlap_src_px`: 인물 bbox의 빈 공간까지 글자 영역을 넓힘(간격은 실루엣 기준으로 검사).
- `decor`는 `background: flat` 스타일에서만 동작한다. 사진·질감 배경은 crop+LaMa이며, flat 적합도(잔차·배경 비율)가 낮으면 자동으로 crop으로 되돌아간다.
- 자리가 없는 `optional` 장식은 인벤토리에 `DROPPED(사유)`로 보고된다. 조용한 손실은 없어야 한다.
