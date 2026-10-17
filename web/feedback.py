"""피드백 학습: 사용자의 "앞으로 이렇게 해줘"를 규칙으로 쌓고, 좋아요 결과를 참고 자료로 모은다.

저장은 feedback/ 폴더(gitignore): rules.json(승인된 규칙), log.jsonl(피드백 기록), liked/<id>/(좋아요 결과).
수치 규칙은 화이트리스트된 디자인 상수만, 범위를 검증해서 받는다. 적용은 환경변수 IMAGEIFX_LEARNED(웹이 만든
서브프로세스에만 줌)로 하므로 CLI로 직접 돌리는 기준 디자인은 바뀌지 않는다."""
import json, os, shutil, threading, time, uuid

from . import store

DIR = os.path.join(store.ROOT, "feedback")
_lock = threading.RLock()

# 에이전트가 건드릴 수 있는 수치 상수: 이름 -> (종류, 최소, 최대, 설명). 이 밖의 키는 거부한다.
KNOBS = {
    "design:layout.text_width_ratio.safe_zone": ("design", 0.40, 0.75, "안전영역 있는 가로 배너에서 글자 블록이 차지하는 폭 비율"),
    "design:layout.text_width_ratio.safe_margin": ("design", 0.35, 0.65, "안전영역 없는 가로형에서 글자 블록 폭 비율"),
    "design:layout.subject_edge_px": ("design", 0, 60, "피사체가 캔버스 가장자리에서 띄우는 px"),
    "design:layout.band_text_pad_px": ("design", 0, 24, "하단 띠 안 문구의 위아래 여백 px"),
    "design:scrim.alpha": ("design", 0.5, 1.0, "글자 뒤 흰 막의 불투명도"),
    "design:scrim.pad_px": ("design", 10, 80, "글자 뒤 흰 막의 바깥 여백 px"),
    "design:movable.decor_scale": ("design", 0.4, 1.0, "재배치하는 장식의 최대 배율"),
    "design:movable.gap_px": ("design", 8, 60, "재배치 장식과 다른 요소의 최소 간격 px"),
    "design:decor.gap_px": ("design", 4, 40, "장식 부품과 글자·인물의 최소 간격 px"),
    "design:decor.max_scale_vs_width": ("design", 0.3, 1.2, "장식 배율 상한(캔버스 폭/원본 폭 대비)"),
    "style:text_scale": ("style", 0.5, 1.2, "글자 블록 크기 배율"),
    "style:person_h_ratio": ("style", 0.3, 1.2, "인물 높이(캔버스 높이 대비)"),
    "style:text_width_ratio": ("style", 0.35, 0.75, "글자 블록 폭 비율"),
    "style:text_v_ratio": ("style", 0.0, 1.0, "글자 블록의 세로 위치(0 위 ~ 1 아래)"),
    "style:bg_top_ratio": ("style", 0.0, 0.5, "배경을 위에서 얼마나 잘라낼지(원본 높이 대비)"),
}
BOOL_KNOBS = {"style:scrim"}                 # true/false
ENUM_KNOBS = {"style:text_side": ("left", "right")}
SCOPES = ("global", "style", "preset")
APPLIES = ("analysis", "layout", "fix")


def _read(path, default):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def rules_path():
    return os.path.join(DIR, "rules.json")


def load_rules():
    return _read(rules_path(), [])


def save_rules(rules):
    with _lock:
        _write(rules_path(), rules)


def log(entry):
    with _lock:
        os.makedirs(DIR, exist_ok=True)
        with open(os.path.join(DIR, "log.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(dict(entry, time=time.strftime("%Y-%m-%d %H:%M:%S")), ensure_ascii=False) + "\n")


def knob_schema_text():
    lines = [f"- {k}: {lo}~{hi} ({doc})" for k, (_, lo, hi, doc) in KNOBS.items()]
    lines += ["- style:scrim: true/false (글자 뒤 흰 막)", "- style:text_side: left/right (글자 쪽)"]
    return "\n".join(lines)


def validate_knobs(knobs):
    """(통과한 {이름: 값}, 거부 사유 목록). 화이트리스트 밖·범위 밖·형식 오류는 거부한다."""
    ok, bad = {}, []
    for k, v in (knobs or {}).items():
        if k in KNOBS:
            _, lo, hi, _ = KNOBS[k]
            try:
                fv = float(v)
            except (TypeError, ValueError):
                bad.append(f"{k}: 숫자가 아님")
                continue
            if not lo <= fv <= hi:
                bad.append(f"{k}: {fv}는 범위 {lo}~{hi} 밖")
                continue
            ok[k] = int(fv) if isinstance(lo, int) and isinstance(hi, int) else round(fv, 4)
        elif k in BOOL_KNOBS and isinstance(v, bool):
            ok[k] = v
        elif k in ENUM_KNOBS and v in ENUM_KNOBS[k]:
            ok[k] = v
        else:
            bad.append(f"{k}: 허용하지 않는 항목")
    return ok, bad


def clean_rule(r):
    """에이전트가 제안한 규칙 dict를 정리한다. 형식이 틀리면 None."""
    if not isinstance(r, dict) or not str(r.get("text", "")).strip():
        return None
    scope = r.get("scope") if r.get("scope") in SCOPES else "global"
    applies = [a for a in (r.get("applies_to") or []) if a in APPLIES] or ["layout", "fix"]
    knobs, bad = validate_knobs(r.get("knobs"))
    return {"text": str(r["text"]).strip()[:300], "scope": scope, "target": str(r.get("target") or "")[:60],
            "applies_to": applies, "knobs": knobs, "rejected_knobs": bad}


def add_rules(rules, source):
    """승인된 규칙을 저장한다. 저장된 규칙 id 목록 반환."""
    ids = []
    with _lock:
        cur = load_rules()
        for r in rules:
            rid = "r" + uuid.uuid4().hex[:6]
            cur.append({"id": rid, "text": r["text"], "scope": r["scope"], "target": r["target"], "applies_to": r["applies_to"],
                        "knobs": r["knobs"], "enabled": True, "source": source, "created": time.strftime("%Y-%m-%d %H:%M:%S")})
            ids.append(rid)
        save_rules(cur)
    return ids


def set_enabled(rid, on):
    with _lock:
        cur = load_rules()
        for r in cur:
            if r["id"] == rid:
                r["enabled"] = bool(on)
        save_rules(cur)


def delete_rule(rid):
    with _lock:
        save_rules([r for r in load_rules() if r["id"] != rid])


def _matches(r, style, preset):
    if not r.get("enabled", True):
        return False
    return r["scope"] == "global" or (r["scope"] == "style" and r["target"] == style) or (r["scope"] == "preset" and r["target"] == preset)


def overlay_for(style, preset, extra_rules=()):
    """이 스타일·사이즈에 적용되는 수치 규칙을 합친 덮어쓰기. 나중 규칙이 이긴다. extra_rules는 아직 저장 전인 후보."""
    design, sty, ids = {}, {}, []
    for r in list(load_rules()) + [dict(x, enabled=True, id="candidate") for x in extra_rules]:
        if not _matches(r, style, preset):
            continue
        for k, v in r.get("knobs", {}).items():
            kind, _, name = k.partition(":")
            (design if kind == "design" else sty)[name] = v
        if r.get("knobs"):
            ids.append(r["id"])
    return {"design": design, "style": sty, "rule_ids": ids} if (design or sty) else {}


def write_overlay(path, overlay):
    """덮어쓰기를 파일로 저장하고 경로를 돌려준다. 비어 있으면 None(= 환경변수를 주지 않는다)."""
    if not overlay:
        return None
    _write(path, overlay)
    return path


def rules_text(kind, style=None, preset=None, extra=()):
    """에이전트 프롬프트에 넣을 글 규칙(applies_to에 kind 포함)."""
    out = []
    for r in list(load_rules()) + [dict(x, enabled=True) for x in extra]:
        if kind not in r.get("applies_to", []) or not r.get("enabled", True):
            continue
        if style is None and preset is None:   # 분석 단계: 스타일을 아직 모르므로 소재 전체·스타일 규칙을 모두 준다
            ok = r["scope"] in ("global", "style")
        else:
            ok = _matches(r, style, preset)
        if ok:
            out.append(r["text"])
    return out


# ---- 좋아요 ----
def liked_dir():
    return os.path.join(DIR, "liked")


def like(job, preset, res, note=""):
    """좋아요 결과를 feedback/liked/<id>/ 에 보관한다(최종 이미지, layout, 분석 json, 메타)."""
    lid = f"{job['id']}_{preset}"
    dst = os.path.join(liked_dir(), lid)
    os.makedirs(dst, exist_ok=True)
    wd = store.ab(res["workdir"])
    final = store.ab(res["final"])
    shutil.copy(final, os.path.join(dst, "final" + os.path.splitext(final)[1]))
    shutil.copy(os.path.join(wd, "layout.json"), os.path.join(dst, "layout.json"))
    ap = os.path.join(store.job_dir(job["id"]), "analysis.json")
    if os.path.exists(ap):
        shutil.copy(ap, os.path.join(dst, "analysis.json"))
    meta = {"id": lid, "job": job["id"], "preset": preset, "style": job.get("style", ""), "note": note[:300],
            "fails": res.get("fails", 0), "warns": res.get("warns", 0), "time": time.strftime("%Y-%m-%d %H:%M:%S")}
    _write(os.path.join(dst, "meta.json"), meta)
    log({"type": "like", **meta})
    return lid


def unlike(lid):
    shutil.rmtree(os.path.join(liked_dir(), lid), ignore_errors=True)


def list_liked():
    d = liked_dir()
    if not os.path.isdir(d):
        return []
    out = [_read(os.path.join(d, x, "meta.json"), None) for x in os.listdir(d)]
    return sorted([m for m in out if m], key=lambda m: m["time"], reverse=True)


def liked_analyses(limit=2):
    """분석 예시로 쓸 좋아요 분석 json(최신순). (스타일, 분석 dict) 목록."""
    out = []
    for m in list_liked():
        a = _read(os.path.join(liked_dir(), m["id"], "analysis.json"), None)
        if a and a.get("elements") and a["style"] not in [s for s, _ in out]:
            out.append((a["style"], a))
        if len(out) >= limit:
            break
    return out


def liked_layout_text(style, preset, limit=2):
    """수정 에이전트에 줄 좋아요 배치 요약(텍스트). 레이어 id·종류와 캔버스 대비 상대 위치·크기만."""
    lines = []
    for m in list_liked():
        if m["preset"] != preset and m["style"] != style:
            continue
        lay = _read(os.path.join(liked_dir(), m["id"], "layout.json"), None)
        if not lay:
            continue
        W, H = lay["canvas"]
        rows = []
        for L in lay["layers"]:
            x0, y0, x1, y1 = L["src_bbox"]
            w, h = round((x1 - x0) * L["scale"]), round((y1 - y0) * L["scale"])
            rows.append(f"{L['type']} 위치({L['x'] / W:.2f},{L['y'] / H:.2f}) 크기({w / W:.2f}x{h / H:.2f})")
        lines.append(f"- 좋아요 {m['preset']} [{m['style']}] {m['note'] or ''}: " + "; ".join(rows))
        if len(lines) >= limit:
            break
    return "\n".join(lines)
