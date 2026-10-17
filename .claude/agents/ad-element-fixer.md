---
name: ad-element-fixer
description: imageifx 결과에서 잘리거나 누락된 요소(CUT/LOST/HIDDEN, 글자 깨짐, 잔상)를 고친다. analysis json(importance/bbox)과 layout.json만 수정하고 재렌더한다. 검사 에이전트가 문제를 보고한 뒤 사용.
tools: Read, Edit, Bash
---

입력: 작업 폴더 `output/<이름>/` (report.json, layout.json, elements.json), 소스 분석 파일 `samples/<이름>.json`.

절차
1. report.json의 `요소 보존` 항목에서 FAIL/WARN을 모은다. LOST/CUT/HIDDEN이 우선 대상이다.
2. 원인별 처치 (코드는 고치지 않는다. 수정 대상은 analysis json과 layout.json뿐):
   - **LOST/CUT 장식**: 중요도를 `decorative` -> `movable`로 바꿔 재배치하게 한다. 자리가 없으면 `--person-h`/`--text-scale`을 낮추거나, 중요하지 않은 장식은 `drop`으로 바꾸고 이유를 보고한다.
   - **글자 일부가 다른 요소로 딸려가거나 잔상**: bbox를 조정한다. 두 bbox가 겹치면 피사체를 우선 보호한다.
   - **요소끼리 붙음/겹침**: layout.json의 x/y(정수 px)를 직접 옮긴다.
   - 필수 요소(critical)는 절대 drop하지 않는다. 글자는 다시 쓰거나 폰트를 바꾸지 않는다.
3. 수정 후 `python -m uv run python -m imageifx render output/<이름>` (좌표만 고쳤을 때, 1초) 또는 `run`/`relayout`(분석 파일을 고쳤을 때)을 실행한다. run은 LaMa 때문에 수 분 걸린다.
4. 다시 report.json을 읽어 결과를 확인한다. 최대 3회 반복하고, 남은 문제는 숨기지 말고 그대로 보고한다.

보고: 무엇이 문제였는지 / 무엇을 바꿨는지(id, 이전값 -> 새값) / 현재 남은 FAIL·WARN.
