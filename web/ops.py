"""작업 폴더(output/<이름>/) 조작: 새 버전 폴더 만들기, layout.json 편집 연산(이동·배율·스크림), 렌더 실행, 결과 요약.

수정은 항상 '복사한 새 폴더'에서 한다. 원래 폴더는 그대로 남으므로 되돌리기는 결과 포인터만 바꾸면 된다.
글자는 다시 그리지 않고 이동·균등 배율만 바꾼다(절대 규칙 1)."""
import json, os, shutil, subprocess, sys

from PIL import Image

from imageifx import spec
from . import store

MOVE_STEP = 16          # 빠른 조정 한 번당 이동 px
SCALE_UP, SCALE_DOWN = 1.08, 0.92
GROUPS = ("text", "subject")


def run_cli(args, learned=None):
    """imageifx CLI 실행. learned가 있으면 그 덮어쓰기(피드백 학습)를 이 실행에만 적용한다."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    env.pop("IMAGEIFX_LEARNED", None)
    if learned:
        env["IMAGEIFX_LEARNED"] = learned
    p = subprocess.run([sys.executable, "-m", "imageifx", *args], cwd=store.ROOT, capture_output=True, text=True,
                       encoding="utf-8", env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def read_layout(workdir):
    with open(os.path.join(workdir, "layout.json"), encoding="utf-8") as f:
        return json.load(f)


def write_layout(workdir, layout):
    with open(os.path.join(workdir, "layout.json"), "w", encoding="utf-8") as f:
        json.dump(layout, f, indent=1)


def find_final(workdir):
    """최종 이미지: layout.json의 name으로 만든 png/jpg 중 가장 최근 것(용량 초과 시 jpg가 나중에 써짐)."""
    name = read_layout(workdir)["name"]
    c = [os.path.join(workdir, name + e) for e in (".png", ".jpg") if os.path.exists(os.path.join(workdir, name + e))]
    return max(c, key=os.path.getmtime) if c else None


def summarize(workdir):
    final = find_final(workdir)
    rep = os.path.join(workdir, "report.json")
    res = {"workdir": store.rel(workdir), "final": store.rel(final) if final else "", "report": "", "fails": 0, "warns": 0,
           "size_kb": round(os.path.getsize(final) / 1000) if final else 0, "v": int(os.path.getmtime(final)) if final else 0}
    if os.path.exists(rep):
        with open(rep, encoding="utf-8") as f:
            rows = json.load(f)
        res.update(report=store.rel(rep), fails=sum(1 for r in rows if not r["ok"]),
                   warns=sum(1 for r in rows if r["ok"] and r.get("warn")))
    return res


def copy_version(src, dst):
    if os.path.exists(dst):
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    return dst


def render(workdir):
    """layout.json대로 다시 렌더하고 report.json을 만든다. FAIL이 있어도(종료코드 1) 산출물은 있다."""
    code, out = run_cli(["render", workdir])
    ok = code in (0, 1) and os.path.exists(os.path.join(workdir, "report.json"))
    return ok, out


def load_layers(workdir, layout):
    return {L["id"]: Image.open(os.path.join(workdir, f"layer_{L['id']}.png")) for L in layout["layers"]
            if os.path.exists(os.path.join(workdir, f"layer_{L['id']}.png"))}


# ---- layout 편집 연산 ----
def box(L):
    x0, y0, x1, y1 = L["src_bbox"]
    return [L["x"], L["y"], L["x"] + round((x1 - x0) * L["scale"]), L["y"] + round((y1 - y0) * L["scale"])]


def group_layers(layout, group):
    """text: 푸터 문구를 뺀 글자 레이어. subject: 글자·장식이 아닌 필수 피사체."""
    if group == "text":
        return [L for L in layout["layers"] if L["type"] == "text" and L.get("group") != "footer"]
    return [L for L in layout["layers"] if L["type"] not in ("text", "decor") and L["importance"] == "critical"]


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def set_scrim(layout, on):
    """스크림(글자 뒤 반투명 면)을 글자 블록 크기에 맞춰 켜거나 끈다."""
    if not on:
        layout.pop("scrim", None)
        return
    tl = group_layers(layout, "text")
    if not tl:
        return
    u = _union([box(L) for L in tl])
    pad = spec.get("scrim.pad_px")
    layout["scrim"] = {"box": [u[0] - pad, u[1] - pad, u[2] + pad, u[3] + pad],
                       "alpha": spec.get("scrim.alpha"), "feather": spec.get("scrim.feather_px")}


def transform_text_block(layout, dx=0, dy=0, factor=1.0):
    """글자 블록 전체를 중심 기준으로 균등 배율한 뒤 이동한다. 정렬 관계가 유지된다."""
    tl = group_layers(layout, "text")
    if not tl:
        return
    u = _union([box(L) for L in tl])
    cx, cy = (u[0] + u[2]) / 2, (u[1] + u[3]) / 2
    had_scrim = "scrim" in layout
    for L in tl:
        L["x"] = int(round(cx + (L["x"] - cx) * factor + dx))
        L["y"] = int(round(cy + (L["y"] - cy) * factor + dy))
        L["scale"] = round(L["scale"] * factor, 4)
    if had_scrim:
        set_scrim(layout, True)


def _scale_layer_about(L, ax, ay, factor):
    L["x"] = int(round(ax + (L["x"] - ax) * factor))
    L["y"] = int(round(ay + (L["y"] - ay) * factor))
    L["scale"] = round(L["scale"] * factor, 4)


def apply_op(layout, op):
    """한 연산을 layout dict에 적용한다. 허용하지 않는 연산은 ValueError. importance 연산은 여기서 다루지 않는다."""
    kind = op.get("op")
    target = op.get("id")
    group = op.get("group")
    if kind == "scrim":
        set_scrim(layout, bool(op.get("on")))
        return
    if group:
        if group not in GROUPS:
            raise ValueError(f"group은 {GROUPS} 중 하나여야 함: {group}")
        layers = group_layers(layout, group)
    else:
        layers = [L for L in layout["layers"] if L["id"] == target]
    if not layers:
        raise ValueError(f"대상 레이어가 없음: {target or group}")
    if kind == "move":
        dx, dy = int(round(float(op.get("dx", 0)))), int(round(float(op.get("dy", 0))))
        if abs(dx) > 400 or abs(dy) > 400:
            raise ValueError("이동은 한 번에 400px까지")
        for L in layers:
            L["x"] += dx
            L["y"] += dy
        if "scrim" in layout and any(L["type"] == "text" for L in layers):
            set_scrim(layout, True)
    elif kind == "scale":
        f = float(op.get("factor", 1.0))
        if not 0.5 <= f <= 1.3:
            raise ValueError("배율은 0.5~1.3")
        if layers[0]["type"] == "text":
            if group:
                transform_text_block(layout, 0, 0, f)
            else:
                b = box(layers[0])
                _scale_layer_about(layers[0], (b[0] + b[2]) / 2, (b[1] + b[3]) / 2, f)
                if "scrim" in layout:
                    set_scrim(layout, True)
        else:   # 피사체는 바닥 중앙 기준(바닥에 붙은 인물이 떠오르지 않게)
            u = _union([box(L) for L in layers])
            ax, ay = (u[0] + u[2]) / 2, u[3]
            for L in layers:
                _scale_layer_about(L, ax, ay, f)
    else:
        raise ValueError(f"허용하지 않는 연산: {kind}")
    layout.setdefault("manual_ops", []).append(op)


QUICK = {   # 빠른 조정 버튼 -> 연산 (토큰 0)
    "text_up": {"op": "move", "group": "text", "dx": 0, "dy": -MOVE_STEP},
    "text_down": {"op": "move", "group": "text", "dx": 0, "dy": MOVE_STEP},
    "text_left": {"op": "move", "group": "text", "dx": -MOVE_STEP, "dy": 0},
    "text_right": {"op": "move", "group": "text", "dx": MOVE_STEP, "dy": 0},
    "text_bigger": {"op": "scale", "group": "text", "factor": SCALE_UP},
    "text_smaller": {"op": "scale", "group": "text", "factor": SCALE_DOWN},
    "subject_bigger": {"op": "scale", "group": "subject", "factor": SCALE_UP},
    "subject_smaller": {"op": "scale", "group": "subject", "factor": SCALE_DOWN},
    "scrim_on": {"op": "scrim", "on": True},
    "scrim_off": {"op": "scrim", "on": False},
}
QUICK_LABELS = [("text_up", "글자 위로"), ("text_down", "글자 아래로"), ("text_left", "글자 왼쪽"), ("text_right", "글자 오른쪽"),
                ("text_bigger", "글자 크게"), ("text_smaller", "글자 작게"), ("subject_bigger", "인물 크게"),
                ("subject_smaller", "인물 작게"), ("scrim_on", "글자 뒤 흰 막 켜기"), ("scrim_off", "흰 막 끄기")]


def apply_ops(workdir, ops):
    """workdir의 layout.json에 연산들을 적용하고 저장한다. (적용한 연산, 거부된 연산[(op, 사유)]) 반환."""
    layout = read_layout(workdir)
    done, bad = [], []
    for op in ops:
        try:
            apply_op(layout, op)
            done.append(op)
        except (ValueError, TypeError, KeyError) as e:
            bad.append((op, str(e)))
    write_layout(workdir, layout)
    return done, bad
