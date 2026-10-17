"""분석 json 검증·환산, AI 호출용 증거(이미지 1장 + 짧은 텍스트) 만들기, AI가 답한 연산 검증."""
import json, os

from PIL import Image, ImageDraw

from imageifx import spec
from . import ops, store

PROMPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
IMPORTANCE = ("critical", "movable", "decor", "band", "drop")
MAX_SIDE = 1024          # 에이전트에게 보여 주는 이미지의 긴 변(px). 이미지 토큰 ≈ 가로×세로÷750
MAX_AI_OPS = 8
ANALYZER_VERSION = "v3"   # 분석 프롬프트가 바뀌면 올려서 예전 분석 캐시를 쓰지 않게 한다
EXAMPLE_FILES = ("grad_ai_english", "tesol_v2", "real1", "grad_global")   # samples/ 의 사람이 검증한 분석(스타일별 1개)


def prompt_text(name):
    with open(os.path.join(PROMPTS, name), encoding="utf-8") as f:
        return f.read().strip()


def analyzer_system_prompt(exclude=()):
    """스타일 설명(presets/styles/*.json의 _doc)과 검증된 분석 예시를 읽어 분석 프롬프트를 만든다. 하드코딩하지 않는다."""
    nl = chr(10)
    styles = nl.join(f"- {n}: {spec.style(n).get('_doc', '')}" for n in spec.style_names())
    ex = []
    for name in [n for n in EXAMPLE_FILES if n not in exclude]:   # exclude: 평가용(같은 소재의 정답을 예시에서 뺀다)
        p = os.path.join(spec.ROOT, "samples", name + ".json")
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                ex.append(f"예시 {len(ex) + 1} ({name}):" + nl + json.dumps(json.load(f), ensure_ascii=False, separators=(",", ":")))
    return prompt_text("analyzer.md").replace("{{STYLES}}", styles).replace("{{EXAMPLES}}", (nl * 2).join(ex))


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


def make_small(src, out):
    """에이전트용 축소본을 저장하고 (원본/축소본) 배율을 돌려준다. 이미 작으면 그대로 복사."""
    im = Image.open(src).convert("RGB")
    f = max(1.0, max(im.size) / MAX_SIDE)
    if f > 1:
        im = im.resize((round(im.width / f), round(im.height / f)), Image.LANCZOS)
    im.save(out)
    return f, im.size


def scale_analysis(data, factor, size, pad=2):
    """축소본 좌표로 받은 bbox를 원본 크기로 환산하고(패딩 포함) 이미지 안으로 맞춘다."""
    W, H = size
    for e in data.get("elements", []):
        b = e.get("bbox")
        if isinstance(b, list) and len(b) == 4 and all(isinstance(v, (int, float)) for v in b):
            e["bbox"] = [max(0, round(b[0] * factor) - pad), max(0, round(b[1] * factor) - pad),
                         min(W, round(b[2] * factor) + pad), min(H, round(b[3] * factor) + pad)]
    return data


def draw_preview(image_path, analysis, out_path):
    im = Image.open(image_path).convert("RGB")
    d = ImageDraw.Draw(im)
    colors = {"critical": (255, 0, 0), "movable": (0, 160, 255), "decor": (0, 200, 0), "band": (255, 160, 0), "drop": (150, 150, 150)}
    for e in analysis["elements"]:
        c = colors.get(e["importance"], (255, 0, 255))
        d.rectangle(e["bbox"], outline=c, width=3)
        d.text((e["bbox"][0] + 4, e["bbox"][1] + 4), f"{e['id']} ({e['importance']})", fill=c)
    im.save(out_path)


def analyzer_message(size_small):
    return f"source.png({size_small[0]}x{size_small[1]})를 Read로 열어 요소를 분석하고 JSON 블록으로 답하라."


# ---- AI 수정 증거 ----
def evidence(preset_key, preset, res, request, agent_dir, tag):
    """AI에게 줄 (프롬프트, 이미지 경로). 이미지는 안전영역·레이어 박스가 그려진 debug.png 한 장(긴 변 1024px 이하)."""
    wd = store.ab(res["workdir"])
    view = os.path.join(agent_dir, f"view_{tag}.png")
    make_small(os.path.join(wd, "debug.png"), view)
    layout = ops.read_layout(wd)
    rows = []
    rp = os.path.join(wd, "report.json")
    if os.path.exists(rp):
        with open(rp, encoding="utf-8") as f:
            rows = [r for r in json.load(f) if not r["ok"] or r.get("warn")]
    rows.sort(key=lambda r: (r["ok"], r["check"]))
    flines = "\n".join(f"- {'WARN' if r['ok'] else 'FAIL'} {r['check']}: {r['detail'][:110]}" for r in rows[:8]) or "- (없음)"
    layers = "\n".join(f"- {L['id']} {L['type']}/{L['importance']} 박스 {ops.box(L)} 배율 {L['scale']}" for L in layout["layers"])
    z = preset.get("safe_zone")
    zone = f"안전영역 상{z['top']} 하{z['bottom']} 좌{z['left']} 우{z['right']}" if z else f"여백 {preset['safe_margin']['x']}px"
    ask = request.strip() or "(요청 없음: FAIL/WARN과 눈에 보이는 결함을 스스로 찾아 고쳐라)"
    msg = (f"매체 {preset_key} {preset['size'][0]}x{preset['size'][1]}, {zone}, 최소 간격 {preset['min_gap']}px.\n"
           f"이미지 축척: 아래 좌표는 원본 크기(캔버스) 기준이고 view_{tag}.png는 축소본이다.\n\n레이어:\n{layers}\n\n"
           f"규정 FAIL/WARN:\n{flines}\n\n사용자 요청: {ask}\n\nview_{tag}.png를 Read로 열어 확인하고 JSON 블록으로 답하라.")
    return msg, view


def parse_ai_ops(data, layout, elements):
    """AI 답변 dict -> (적용할 연산, drop할 장식 id 목록, 거부 사유 목록, notes)."""
    good, drops, bad = [], [], []
    ids = {L["id"] for L in layout["layers"]}
    by_id = {e["id"]: e for e in elements}
    for op in (data or {}).get("ops", [])[:MAX_AI_OPS]:
        if not isinstance(op, dict):
            bad.append(f"연산 형식 오류: {op!r}")
            continue
        if op.get("op") == "drop":
            e = by_id.get(op.get("id"))
            if not e or e["importance"] not in ("movable", "decor"):
                bad.append(f"drop 거부(필수 요소이거나 없음): {op.get('id')}")
            else:
                drops.append(e["id"])
            continue
        if op.get("op") in ("move", "scale") and not op.get("group") and op.get("id") not in ids:
            bad.append(f"없는 레이어: {op.get('id')}")
            continue
        good.append(op)
    return good, drops, bad, str((data or {}).get("notes") or "")[:300]
