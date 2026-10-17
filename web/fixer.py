"""분석 json 검증, 에이전트 프롬프트 조립, 수정 전 스냅샷·복원, 금지 경로 보호."""
import hashlib, json, os, shutil, subprocess

from PIL import Image, ImageDraw

from imageifx import spec
from . import store

TYPES = ("text", "subject", "person", "product", "logo", "decor", "decorative")
IMPORTANCE = ("critical", "movable", "decor", "band", "drop")
PROTECTED = ("imageifx", "presets", "baselines", "samples", "docs", ".claude", "web", "tests")


# ---- 분석 json ----
def validate_analysis(data, size):
    """문제 목록을 반환한다(빈 리스트면 통과)."""
    errs = []
    if not isinstance(data, dict):
        return ["최상위가 객체가 아닙니다"]
    styles = spec.style_names()
    if data.get("style") not in styles:
        errs.append(f"style이 없거나 잘못됨: {data.get('style')!r} (사용 가능: {', '.join(styles)})")
    els = data.get("elements")
    if not isinstance(els, list) or not els:
        return errs + ["elements가 비어 있습니다"]
    W, H = size
    seen = set()
    for e in els:
        eid = e.get("id") if isinstance(e, dict) else None
        if not eid or eid in seen:
            errs.append(f"id가 없거나 중복됨: {eid!r}")
            continue
        seen.add(eid)
        if e.get("importance") not in IMPORTANCE:
            errs.append(f"[{eid}] importance는 {IMPORTANCE} 중 하나여야 함: {e.get('importance')!r}")
        if not e.get("type"):
            errs.append(f"[{eid}] type이 없음")
        b = e.get("bbox")
        if not (isinstance(b, list) and len(b) == 4 and all(isinstance(v, (int, float)) for v in b)):
            errs.append(f"[{eid}] bbox가 [x0,y0,x1,y1] 형식이 아님")
        elif not (0 <= b[0] < b[2] <= W and 0 <= b[1] < b[3] <= H):
            errs.append(f"[{eid}] bbox {b}가 이미지({W}x{H}) 밖이거나 뒤집힘")
    if not any(e.get("importance") == "critical" for e in els if isinstance(e, dict)):
        errs.append("critical 요소가 하나도 없음")
    return errs


def draw_preview(image_path, analysis, out_path):
    im = Image.open(image_path).convert("RGB")
    d = ImageDraw.Draw(im)
    colors = {"critical": (255, 0, 0), "movable": (0, 160, 255), "decor": (0, 200, 0), "band": (255, 160, 0), "drop": (150, 150, 150)}
    for e in analysis["elements"]:
        c = colors.get(e["importance"], (255, 0, 255))
        d.rectangle(e["bbox"], outline=c, width=3)
        d.text((e["bbox"][0] + 4, e["bbox"][1] + 4), f"{e['id']} ({e['importance']})", fill=c)
    im.save(out_path)


def analyzer_prompt(image_abs, out_abs, size, errors=None):
    styles = ", ".join(spec.style_names())
    p = f"""광고 이미지 `{image_abs}` ({size[0]}x{size[1]} px)를 Read로 열어 눈으로 보고 분석 json을 `{out_abs}` 에 Write로 저장하라.
(samples/ 에 저장하지 말 것. 위 경로에만 저장한다. 이 이미지는 처음 보는 새 소재이므로 기존 예시 소재를 가정하지 말 것.)

형식: {{"style": "<{styles} 중 하나>", "elements": [{{"id": 영문소문자_숫자, "type": "text|subject|decor", "importance": "critical|movable|decor|band|drop", "bbox": [x0,y0,x1,y1]}}]}}
- style: dark_board(어두운 칠판) / photo_collage(밝은 사진·콜라주) / bold_solid(단색 배경+굵은 큰 글씨) / dark_photo(어두운 단색 영역에 글씨, 한쪽에 인물 사진).
- importance: critical=글자·인물(안전영역 규칙 대상), movable=재배치할 장식(배경에서 제거), decor=장식을 부품으로 복사(flat 스타일 전용), band=전체 폭 띠, drop=지우기만.
- 글자는 줄이 아니라 의미 블록(제목/서브/CTA/날짜) 단위, bbox는 글자보다 4~8px 넉넉히. 안내 문구(*AI 활용 이미지 등)는 "group": "footer".
- 좌표는 원본 픽셀. 추측하지 말고 bbox를 그려 확인 이미지를 만들어 Read로 다시 열어 검증한다.
- 마지막 답변에는 저장한 경로와 한 줄 요약만 쓴다(json 전체를 답변에 쓰지 말 것)."""
    if errors:
        p += "\n\n이전 시도의 검증 오류. 반드시 고칠 것:\n- " + "\n- ".join(errors)
    return p


def fixer_prompt(job, preset, res, request, fix_n):
    wd = res["workdir"]
    rep = []
    rp = store.ab(res["report"]) if res.get("report") else None
    if rp and os.path.exists(rp):
        with open(rp, encoding="utf-8") as f:
            rep = [r for r in json.load(f) if not r["ok"] or r.get("warn")]
    lines = "\n".join(f"- {'WARN' if r['ok'] else 'FAIL'} {r['check']}: {r['detail']}" for r in rep) or "- (FAIL/WARN 없음)"
    ask = request.strip() or "(사용자 요청 없음: report의 FAIL/WARN과 compare.png·debug.png에서 눈으로 보이는 결함을 스스로 찾아 고쳐라.)"
    return f"""imageifx 웹의 '수정 요청'이다. 작업 {job['id']}, 매체 {preset}, 수정 {fix_n}회차.

작업 폴더: output/{wd}  (layout.json, elements.json, report.json, compare.png, debug.png, 최종 이미지)
이 작업의 분석 json: output/_jobs/{job['id']}/analysis.json
최종 이미지: output/{res['final']}

사용자 요청:
{ask}

현재 report의 FAIL/WARN:
{lines}

절차: (1) report.json, compare.png, debug.png, 최종 이미지를 Read로 직접 열어 눈으로 확인한다. (2) 좌표만 고치면 output/{wd}/layout.json 을 Edit 후
`python -m uv run python -m imageifx render output/{wd}`, bbox·중요도를 고치면 분석 json 과 output/{wd}/elements.json 을 함께 Edit 후
`python -m uv run python -m imageifx relayout output/{wd} --tag fx{fix_n} --preset {preset}` (새 폴더가 생긴다). (3) `verify`로 검증하고 결과를 다시 연다. 최대 3회 반복.
수정해도 되는 파일은 output/ 아래의 이 작업 파일뿐이다. imageifx/, presets/, baselines/, samples/, 코드는 절대 수정하지 말 것.
글자를 다시 그리거나 폰트·내용을 바꾸지 말 것(이동·균등 축소만). 배경 확장에 블러·단색 늘리기 금지. 요청이 규정(안전영역·용량·크기 등)과 충돌하면 고치지 말고 이유를 보고한다.
hard FAIL이 남으면 최종본이 아니라고 보고한다. "네이버 검수 통과"라고 쓰지 말 것.

마지막 답변 끝에 다음 JSON 블록 하나를 출력한다:
```json
{{"final_workdir": "새 최종 작업 폴더 이름(output/ 아래, 같은 폴더면 그대로)", "changes": [{{"id": "요소id", "field": "x", "before": 0, "after": 0, "why": "이유"}}], "remaining_fail": ["남은 FAIL 항목"], "notes": "사용자에게 한 줄 설명"}}
```"""


# ---- 스냅샷·복원 ----
def snapshot(jid, preset, n, res):
    """수정 전 작업 폴더를 통째로 복사한다. (복사본 상대경로) 반환."""
    src = store.ab(res["workdir"])
    dst = os.path.join(store.job_dir(jid), f"fix{n}_{preset}", "before")
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return store.rel(dst)


def restore(res_before, snap_rel):
    """스냅샷을 원래 작업 폴더에 되돌린다."""
    src, dst = store.ab(snap_rel), store.ab(res_before["workdir"])
    shutil.copytree(src, dst, dirs_exist_ok=True)


def file_md5(path):
    with open(path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


# ---- 금지 경로 보호 ----
def protected_state():
    """보호 경로의 변경 상태 지문(git). 에이전트 실행 전후로 비교한다."""
    try:
        st = subprocess.run(["git", "status", "--porcelain", "--", *PROTECTED], cwd=store.ROOT, capture_output=True, text=True, encoding="utf-8").stdout
        df = subprocess.run(["git", "diff", "HEAD", "--", *PROTECTED], cwd=store.ROOT, capture_output=True, text=True, encoding="utf-8").stdout
    except OSError:
        return None
    return hashlib.md5((st + df).encode("utf-8")).hexdigest()
