# imageifx — Codex 인계 문서

이 파일 하나만 읽으면 이어서 작업할 수 있게 정리했다. 작업 폴더는 `C:\Users\whtkd\imageifx`이다.
Codex가 자동으로 읽게 하려면 이 파일을 저장소 루트에 `AGENTS.md`로 복사한다.

사용자는 한국어를 쓰는 광고 마케터(개발 비전문가, 비용 민감)이다. 설명은 한국어로, 실무 관점으로 짧게 한다.

## 1. 프로젝트

GPT로 만든 1:1 광고 이미지를 네이버 GFA(최우선), 메타, 구글 사이즈로 변환하는 내부용 도구. 로컬 CPU에서 무료로 동작한다.
- 전부 무료. 유료 API 금지. NVIDIA GPU 없음(CPU 전용).
- 최우선 타깃은 **GFA 1250x560, 용량 250KB 미만**(처음 말한 1200x560은 폐기).
- GFA는 글자·주요 디자인만 안전영역 안이면 되고, 장식은 밖으로 나가거나 잘려도 된다. 메타·구글은 규정 없이 자연스러운 재배열.
- 웹 UI는 사용자 결정으로 폐기했다(커밋 `243ca1d`까지 이력에 있음). 앞으로 CLI와 이미지 직접 확인으로만 진행한다.

## 2. 절대 규칙 (어기지 말 것)

1. **글자 픽셀을 다시 그리거나 폰트·내용을 바꾸지 않는다.** 글자는 원본에서 레이어로 추출해 이동·균등 축소만 한다.
2. **배경 확장에 블러·단색 늘리기를 쓰지 않는다.** 위/아래 띠만 비는 평평한 배경은 가장자리 색 연장, 그 외는 LaMa.
3. **규정 hard FAIL이 있으면 최종본이 아니다.** 안전영역, 용량, 크기, 흰색 면적, 배경색, 가장자리 채움, 글자 잘림은 hard이다.
4. **기준(baseline) 디자인은 사용자 승인 없이 바꾸지 않는다.** 두 기준(테솔, 한국어학과)은 사용자가 "이대로"라고 확정했다. 코드나 규칙을 고친 뒤 기준과 달라지면 의도한 변경인지 먼저 사용자에게 묻는다.
5. 규칙 신뢰도(A/B/C)를 섞어 말하지 않는다. GFA 안전영역·흰색·배경색 규칙은 단일 출처(B)이고 광고관리자 확인 전에는 "확정"이라 하지 않는다. "실제 네이버 검수 통과"를 보장한다고 말하지 않는다.
6. 이미지는 결과를 **직접 열어서** 확인한다. report.json의 PASS만 믿지 않는다(코드 검사가 놓친 결함이 눈으로 여러 번 발견됐다).

## 3. 환경과 명령

- Windows. Python 3.12 고정(`uv`). 시스템 `python`은 3.14라 의존성이 없다. **반드시 `python -m uv run ...`** 을 쓴다.
- 한글 출력은 `PYTHONIOENCODING=utf-8`. json은 항상 `encoding="utf-8"`로 연다(기본 cp949면 `presets.json`이 깨진다).
- 긴 python 스크립트는 heredoc 대신 파일로 저장해 실행한다(셸 파싱 오류가 났다).
- LaMa 분해(`run`)는 소재당 수 분 걸린다. 좌표만 바꿀 때는 `render`(1초).

| 명령 | 용도 |
|---|---|
| `python -m uv run python -m imageifx run <이미지> <분석json> --preset gfa_1250x560` | 분해부터 전체 실행 |
| `python -m uv run python -m imageifx relayout <작업폴더> --tag X --preset P` | 분해 캐시를 재사용해 새 배치 |
| `python -m uv run python -m imageifx render <작업폴더>` | layout.json 수정 후 재렌더 |
| `python -m uv run python -m imageifx verify <최종파일> --preset P` | 저장된 파일만으로 독립 검증 |
| `python -m uv run pytest -q` | 전체 테스트(LaMa·토큰 불필요, 약 6초) |

단일 테스트: `python -m uv run pytest tests/test_validate.py::이름 -q`.
단위 테스트는 기준 디자인의 시각 품질을 보장하지 않는다. 코드·상수를 고친 뒤에는 `baselines/_md5_before_refactor.txt`의 md5와 `relayout`/`render` 결과를 비교해 기준이 바뀌었는지 확인한다(규칙 4).

## 4. 데이터 흐름

`run`: 원본 + 분석json → `decompose.run`(글자·요소 레이어 PNG + `clean_bg.png`, LaMa) → `layout.build`(스타일 프로필 + 프리셋으로 `layout.json` 생성) → `cli._finish`(`compose.build_bg` 배경 확장 → `compose.render` → `_export` PNG, 용량 초과 시 JPG 품질을 낮춤 → `validate.*` → `report.json`, `debug.png`, `compare.png`).

- 작업 폴더는 `output/<이름>/`(gitignore). `relayout`은 `clean_bg.png`·`layer_*.png`·`decompose_meta.json`을 새 폴더로 복사해 분해를 건너뛴다. `render`는 `layout.json`을 직접 읽어 다시 그린다.
- 상수의 단일 출처
  - 디자인 결과에 영향 주는 값: `presets/design_spec.json` (코드에서 `spec.get("a.b")`)
  - 매체 규격·규칙: `presets/presets.json`
  - 스타일별 배치 옵션: `presets/styles/*.json`
  - 코드에 매직넘버를 새로 넣지 말고 여기에 둔다.
- `output/`의 `.log`·임시 폴더는 개발 중 남은 것이며 기준이 아니다. 기준은 `baselines/`뿐이다.

## 5. 코드 구조

- `imageifx/decompose.py` 글자 영역 자동 보정, 클린 배경, 레이어 추출
- `imageifx/layout.py` GFA 배치(안전영역, 정렬 정규화, 장식 재배치), 세로/정사각 배치
- `imageifx/compose.py` 합성, 배경 확장, 스크림
- `imageifx/validate.py` 규정·글자 무결성·요소 보존·독립 검증
- `imageifx/occlusion.py` 글자가 인물 뒤에 가려진 곳 감지·복원(요소에 `occlusion: "restore"`를 준 경우만, 규칙 1의 사용자 승인 예외)
- `presets/presets.json` 매체 규격과 규칙, `docs/gfa_guide.md` 규칙 근거와 신뢰도, `docs/cases.md` 사례집, `docs/cases/` 기준 이미지
- `baselines/` 기준 디자인(최종 jpg, layout, 분해 캐시), `samples/` 분석 json 예시, `input/` 원본

## 6. 분석 json 작성 규칙

최상단 `"style"`:
- `dark_board` 어두운 칠판 / `photo_collage` 밝은 사진·콜라주 / `bold_solid` 단색 배경 + 굵은 큰 글씨 / `photo_whole` 사진 위에 글자가 얹힌 소재
- 배치 옵션은 스타일이 정하고, CLI 옵션은 덮어쓰기 전용(기록·경고됨).

요소 `importance`:
- `critical` 글자·인물, 안전영역 규칙 대상
- `movable` 재배치할 장식, 배경에서 제거
- `decor` 장식을 부품으로 복사, flat 스타일 전용
- `band` 전체 폭 띠
- `drop` 지우기만

`decor` 속성: `anchor`(corner_tl/tr/bl/br) · `priority`(keep/optional) · `overlap`(none/subject/any) · `z`(back/behind_subject/front) · `polarity`(light/dark/any) · `fade`(경계를 부드럽게, 원본 px).
글자: `group: footer`는 띠 안에 따로 배치. `mask`는 `glyph`(글자 모양대로 지움, 단색 배경용) 또는 `bbox`.
인물: `text_overlap_src_px`로 인물 bbox의 빈 공간까지 글자 영역을 넓힌다(간격은 실루엣 기준으로 검사).

주의
- `decor`는 `background: flat` 스타일에서만 동작한다. 사진·질감 배경은 crop + LaMa이며, flat 적합도가 낮으면 자동으로 crop으로 되돌아간다.
- 자리가 없는 `optional` 장식은 인벤토리에 `DROPPED(사유)`로 보고된다. 조용한 손실은 없어야 한다.
- 텍스트는 줄 단위가 아니라 의미 블록(제목/서브/CTA) 단위로 묶고 bbox를 글자보다 4~8px 넉넉히 잡는다. bbox를 그려 확인용 이미지로 눈 검증한다.

스타일 메모
- `photo_whole`: 글자를 떼지 않고 원본을 통째로 균등 축소(`fit_ratio`)해 글자 묶음을 안전영역에 넣고 가장자리만 확장한다. 글자 레이어는 검사용(`ghost`)이라 합성되지 않는다. 오른쪽 확장은 단계적 LaMa(`fill.outpaint_progressive`) + 글자 없는 풍경 구간은 원본 반사(`fill.mirror_right`, `mirror_gap_px`·`mirror_src_x0`).
- `bold_solid`: `band_tint_ratio`로 거의 흰 하단 띠에 배경색을 섞어 "가장자리 흰색 채움" FAIL을 피한다. 디자인 색 변경이므로 사용자 확인이 필요하다.

## 7. GFA 규칙과 신뢰도 (`docs/gfa_guide.md`가 원본)

- **A (2곳 이상 일치)**: 1250x560, 50~250KB, JPG/PNG(투명 불가)
- **B (한 출처, 공식 원문 인용이라 밝힘)**: 안전영역 상50/하35/좌우240(770x475), 흰색 면적 50% 이하, 배경 #F2F2F2 금지, 가장자리 흰/검정 채움 금지
- **C (실무 경험칙)**: 글자 높이 22~52px, 4줄, 3색, 텍스트 비율 20%
- 공식 원문 `ads.naver.com/adguide/1474`는 조사 도구로 열 수 없었다. 좌우 여백이 한 자료에서 240과 225로 엇갈린다.
- 용량은 1KB=1000바이트로 판정(보수적).
- B/C 규칙을 확정 규정처럼 말하지 않는다. 광고관리자 등록 화면에서 사용자가 확인할 항목을 항상 함께 안내한다.

## 8. 사용자 합의

- 두 기준 디자인: 테솔(어두운 칠판형) `baselines/tesol_v2_gfa_1250x560/`, 한국어학과(사진 콜라주형) `baselines/real1_gfa_1250x560/`.
- 수락된 사항: 올빼미 얼굴이 좌측 240px 경계에 걸림, 한국어학과에서 스크림이 노트 손글씨를 덮음.
- 우선순위: 디자인 스타일 일관성과 GFA 규정 준수 둘 다 매우 중요.
- 이유: 예전에 소재마다 옵션을 손으로 조정해 결과가 달라지고, 코드 수정 때마다 이전 결과가 조용히 바뀌었다.

## 9. 소재 한 장을 끝내는 순서 (검토 게이트)

Claude에서는 `.claude/agents/*.md` 에이전트로 돌렸다. Codex는 이 에이전트를 쓸 수 없으니 아래 체크리스트를 직접 수행한다. 수정하면 검토를 처음부터 다시 한다.

0. **사례 확인**: `docs/cases.md`와 `docs/cases/`에서 비슷한 증상·소재를 먼저 찾는다. 있으면 그때 통한 스타일·키·옵션·명령을 번역해 쓴다. 사용자 승인 필요 항목(글자 복원, 하단 띠 색 변경, 기준 변경)을 따로 표시한다.
1. **요소 분석**: 원본을 눈으로 열어 `samples/<이름>.json` 작성. bbox를 그려 확인 이미지로 재검증.
2. **실행**: `run ...`
3. **요소 수정(필요 시)**: report.json의 `요소 보존` FAIL/WARN(LOST/CUT/HIDDEN) 처치. 코드는 안 고치고 분석 json과 layout.json만 고친다.
   - LOST/CUT 장식: `decorative`→`movable`로 바꿔 재배치. 자리가 없으면 `--person-h`/`--text-scale`을 낮추거나 `drop`(이유 보고).
   - 글자가 딸려가거나 잔상: bbox 조정(피사체 우선 보호). 요소끼리 붙음: layout.json의 x/y(정수 px) 이동.
   - critical은 절대 drop하지 않는다. 최대 3회 반복하고 남은 문제는 숨기지 않고 보고.
4. **자연스러움 점검** (`compare.png`: 왼쪽 원본 | 오른쪽 결과)
   - 원본 요소 전체 목록을 먼저 만들고 결과와 대조(있음/일부 잘림/없음/변형).
   - 글자 무결성: 획 끊김, 번짐, 가장자리 색 테두리, 다른 요소 조각 붙음. 줄 끝 대시·밑줄·낙서가 반쪽만 남았는지. 한쪽 쏠림. 중앙 정렬에서 특정 줄만 밀렸는지. 글자끼리 붙음, 장식이 글자 위에 겹침.
   - 합성: 인물 외곽의 흰 테두리·잔상, clean_bg의 지워진 자국, 의도와 다른 crop(얼굴 잘림).
   - 의심 구간은 PIL로 crop 후 NEAREST 3~4배 확대해 다시 본다. 확대로 확인한 것만 단정한다.
5. **배경 확장 점검**: `layout.json`의 `bg`(x, y, scale)로 원본 범위를 구해 그 밖이 확장 영역. 임시 스크립트로 확장 영역을 crop 후 `(픽셀 - 평균) * 4 + 128` 대비 강조 확대를 만들어 본다.
   - 세로 줄무늬(일정 간격 띠), 수평 번짐, 이음선(경계 양쪽 10px 평균 RGB 비교), 흐림, 복제·반사 흔적, 칼로 자른 듯한 끝, 복원 글자의 울퉁불퉁함, 위/아래 띠 검게 남음.
6. **GFA 규정 검토 (마지막 수정 후 서로 다른 방식으로 3회 연속 통과)**
   - 1회: `render` 후 report.json, FAIL 0건
   - 2회: `verify <최종파일> --preset gfa_1250x560`(크기, 형식, 알파 없음, 50~250KB, 흰색 면적, 가장자리, 배경색을 파일에서 직접 측정)
   - 3회: 최종 이미지를 열어 가이드 규칙표와 항목별 대조. 좌우 240px 구역을 잘라 확대해 글자·로고·CTA 픽셀 유무 확인. 가로 375px로 축소해도 읽히는지.
   - 결과가 서로 어긋나면(코드 PASS인데 눈으로 위반) 도구를 믿지 말고 원인을 찾아 보고. 작업 폴더에 `guide_review.md`로 회차별 방법/측정값/판정을 남긴다.
7. **디자인 총평 (PASS/REWORK)**: 정보 위계(헤드라인 > 서브 > 기간/CTA), 가독성(모바일 크기 확인), 구도·균형, 원본 의도 유지, 배치 충돌. 규정 위반을 디자인 욕심으로 허용하지 않고, 규정 안이라는 이유로 원본 요소를 과도하게 버리지도 않는다. 같은 문제가 3회 이상 남으면 자동 해결을 멈추고 사용자에게 선택지를 올린다.

## 10. 사례집 요약 (`docs/cases.md`)

- 글자가 인물에 가려짐 → 요소에 `occlusion: "restore"` (사용자 승인 필요, 규칙 1 예외)
- 사진 위 글자·붓터치 소재 → 스타일 `photo_whole`
- 확장 흐림·세로 줄무늬 → 풍경은 원본 반사, 하늘은 색 프로필 + 입자(최근 커밋 `470e897`, `cbe54ab`), 나머지 단계적 LaMa
- 새로 푼 사례는 `docs/cases.md`에 추가한다.

## 11. 현재 상태 (2026-10-19 기준)

브랜치 `master`(메인은 `main`). 최근 커밋:
- `470e897` 하늘 확장에서 가장자리 구름을 대칭·가로 확대로 이어 붙임
- `cbe54ab` 하늘 색 모델 확장(세로 줄무늬 방지), 사례집
- `77a9c14` 웹 UI 제거
- `60e8fde` 가림 글자 복원(occlusion), photo_whole, 단계적 LaMa, 하단 띠 틴트

**미커밋 변경 (작업 중이던 것)**
- `imageifx/compose.py`: `_tint_white()` 추가. `bg.white_tint`가 있으면 흰색에 가까운 픽셀(밝기 225~250)만 지정색 쪽으로 섞는다("흰색 면적" 규칙 회피용 옵션, 기본 없음).
- `imageifx/layout.py`: `auto_whole`에 `align_x`(`text_left` 기본 / `text_right` / `flush_right`)와 `white_tint` 옵션 추가. `build()`가 스타일 json의 `align_x`, `white_tint`를 전달.
- 새 스타일: `presets/styles/photo_whole_white.json`, `presets/styles/photo_whole_wide_left.json`
- 새 입력: `input/kakao_a.png`, `kakao_b.png`, `tesol_dl.png`, `yeongwol_dl.png`
- 새 분석 json: `samples/kakao_a_corn.json`, `samples/kakao_b_cyber.json`

이어서 할 일(추정, 사용자 확인 필요): 위 새 입력(kakao_a/b 등)을 `photo_whole_white` / `photo_whole_wide_left` 스타일로 돌려 결과를 눈으로 확인하고, 문제 없으면 커밋. 기본값은 기존 동작을 유지하므로 기준 디자인은 바뀌지 않아야 한다 — 그래도 md5 비교로 확인한다.

## 12. Codex 첫 지시문 예시

> `C:\Users\whtkd\imageifx`에서 작업한다. 이 인계 문서(AGENTS.md)와 `docs/cases.md`, `docs/gfa_guide.md`를 읽는다.
> 먼저 `python -m uv run pytest -q`가 통과하는지, `git status`가 인계 문서의 11장과 같은지 확인하고 보고한다.
> 그다음 `input/kakao_a.png`를 `samples/kakao_a_corn.json`으로 `gfa_1250x560` 프리셋에 돌려 결과 이미지를 직접 열어 확인한다. 기준 디자인이 바뀌면 진행하지 말고 먼저 묻는다.
