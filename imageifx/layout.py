"""GFA 모드 자동 배치. 결과는 정수 px 좌표의 layout.json (이후 사람이 px 단위로 수정).
디자인 상수는 presets/design_spec.json, 소재 유형별 값은 presets/styles/*.json 에서 읽는다."""
from . import spec as _spec

S = _spec.get


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _align_dx(texts, tol=None):
    """중앙 정렬 디자인에서 중심이 크게 어긋난 줄만 가운데로 당길 이동량(원본 px)을 돌려줌.
    좌측 정렬 디자인(왼쪽 끝이 더 가지런함)은 건드리지 않는다.
    같은 높이에 나란히 있는 요소(예: 알약 두 개)는 한 줄로 묶어 같이 움직인다(따로 움직이면 서로 겹침)."""
    import statistics as st
    if tol is None:
        tol = S("layout.align_tolerance")
    if len(texts) < 3:
        return {}
    rows = []
    for e in sorted(texts, key=lambda e: e["bbox"][1]):
        for r in rows:
            ov = min(e["bbox"][3], r["y1"]) - max(e["bbox"][1], r["y0"])
            if ov > 0.5 * min(e["bbox"][3] - e["bbox"][1], r["y1"] - r["y0"]):
                r["els"].append(e)
                r["y0"], r["y1"] = min(r["y0"], e["bbox"][1]), max(r["y1"], e["bbox"][3])
                break
        else:
            rows.append({"els": [e], "y0": e["bbox"][1], "y1": e["bbox"][3]})
    if len(rows) < 3:
        return {}
    spans = [(min(e["bbox"][0] for e in r["els"]), max(e["bbox"][2] for e in r["els"])) for r in rows]
    cs = [(a + b) / 2 for a, b in spans]
    ls = [a for a, _ in spans]
    if st.pstdev(cs) >= st.pstdev(ls):
        return {}
    med = st.median(cs)
    uw = max(b for _, b in spans) - min(a for a, _ in spans)
    out = {}
    for r, c in zip(rows, cs):
        if abs(c - med) > tol * uw:
            for e in r["els"]:
                out[e["id"]] = med - c
    return out


def auto_gfa(elements, src_size, preset, text_side="left", bg_top=None,
             text_scale=1.0, person_h=None, scrim=False, text_width_ratio=None, text_v_ratio=None):
    """bg_top: 캔버스 맨 위에 올 원본 y좌표(콜라주를 얼마나 보일지). None이면 필수요소 중심 자동.
    text_scale: 글자 최대 배율. person_h: 피사체 최대 높이(px). scrim: 글자 뒤 흰 스크림."""
    W, H = preset["size"]
    mx, my = preset["safe_margin"]["x"], preset["safe_margin"]["y"]
    gap = preset["min_gap"]
    sw, sh = src_size
    sz = preset.get("safe_zone")
    if sz:   # 방향별 안전영역(GFA 1250x560: 770x475). 글자는 이 안, 피사체는 바깥으로 나가도 됨
        sx0, sy0, sx1, sy1 = sz["left"], sz["top"], W - sz["right"], H - sz["bottom"]
    else:
        sx0, sy0, sx1, sy1 = mx, my, W - mx, H - my      # 세이프존
    safe_w, safe_h = sx1 - sx0, sy1 - sy0

    crit = [e for e in elements if e["importance"] == "critical"]
    band_el = next((e for e in elements if e["importance"] == "band"), None)   # 하단 안내 띠(전체 폭)
    all_texts = [e for e in crit if e["type"] == "text"]
    footer = [e for e in all_texts if e.get("group") == "footer"] if band_el else []   # 띠 안에 넣을 안내 문구
    # 띠가 없을 때의 안내 문구(group=footer, 예: '*AI 활용 이미지'): 글자 블록과 따로 안전영역 왼쪽 아래 모서리에 원래 크기로 둔다.
    notes = [e for e in all_texts if e.get("group") == "footer"] if not band_el else []
    texts = [e for e in all_texts if e not in footer and e not in notes]
    subjs = [e for e in crit if e["type"] != "text"]
    note_h = max([e["bbox"][3] - e["bbox"][1] for e in notes] + [0])
    note_reserve = (note_h + gap) if notes else 0   # 글자 블록은 이만큼 위에서 끝난다

    # 배경: cover 방식 (캔버스를 가득 채움 -> 확장 불필요), 세로는 필수요소 중심으로 crop
    s = max(W / sw, H / sh)
    cy = (sum((e["bbox"][1] + e["bbox"][3]) / 2 for e in crit) / len(crit)) if crit else sh / 2
    by = int(round(H / 2 - cy * s)) if bg_top is None else -int(round(bg_top * s))
    by = min(0, max(H - int(round(sh * s)), by))
    bx = int(round((W - sw * s) / 2))
    layout = {"bg": {"scale": s, "x": bx, "y": by}, "layers": []}
    band_h = round((band_el["bbox"][3] - band_el["bbox"][1]) * s) if band_el else 0
    floor_y = H - band_h   # 글자·피사체가 서는 바닥(띠가 있으면 띠 윗면)
    if band_el:
        layout["band"] = {"src_bbox": band_el["bbox"], "h": band_h}
    layout["floor"] = {"y": floor_y, "src_y": band_el["bbox"][1] if band_el else sh}   # 장식 앵커의 바닥 기준

    def place(group, x_left, x_right, align):
        """group을 [x_left,x_right] 안에 세로 중앙 정렬로 배치. 균등 축소만 사용."""
        if not group:
            return
        dxs = _align_dx(group) if group[0]["type"] == "text" else {}
        u = _union([[e["bbox"][0] + dxs.get(e["id"], 0), e["bbox"][1], e["bbox"][2] + dxs.get(e["id"], 0), e["bbox"][3]] for e in group])
        uw, uh = u[2] - u[0], u[3] - u[1]
        # 원본 아래 끝에 걸려 있던 피사체는 직선으로 잘려 보이지 않게 캔버스 하단으로 bleed
        on_band = bool(band_el) and group[0]["type"] != "text" and any(e["bbox"][3] >= band_el["bbox"][1] - 3 for e in group)
        bleed_b = group[0]["type"] != "text" and not on_band and any(e["bbox"][3] >= sh - S("layout.bleed_touch_px") or e.get("bleed_bottom") for e in group)   # bleed_bottom: 아래 일부(예: 겹친 안내 문구)를 bbox에서 뺐어도 캔버스 아래로 붙인다
        is_text = group[0]["type"] == "text"
        region_h = (min(safe_h, floor_y - sy0) if band_el else safe_h) - (note_reserve if is_text else 0)
        avail_h = (floor_y - sy0) if on_band else ((H - sy0) if bleed_b else region_h)
        cap = text_scale if is_text else ((person_h / uh) if person_h else 99)
        sc = min((x_right - x_left) / uw, avail_h / uh, cap)
        pw, ph = uw * sc, uh * sc
        ox = x_left if align == "left" else x_right - pw
        vr = 0.5 if (not is_text or text_v_ratio is None) else text_v_ratio   # 글자 블록의 세로 위치(0=위, 0.5=가운데)
        oy = (floor_y - ph) if on_band else ((H - ph) if bleed_b else sy0 + (region_h - ph) * vr)
        start = len(layout["layers"])
        for e in group:
            b = e["bbox"]
            layout["layers"].append({
                "id": e["id"], "type": e["type"], "importance": "critical",
                "src_bbox": b, "scale": round(sc, 4),
                "x": int(round(ox + (b[0] + dxs.get(e["id"], 0) - u[0]) * sc)),
                "y": int(round(oy + (b[1] - u[1]) * sc)),
                **({"bleed": ["bottom"]} if bleed_b else {}),
                **({"on_band": True} if on_band else {}),
            })
        if bleed_b:  # 반올림 오차로 1px 틈이 생기지 않게 하단을 캔버스 끝에 정확히 맞춤
            new = layout["layers"][start:]
            dy = H - max(round(L["y"] + (L["src_bbox"][3] - L["src_bbox"][1]) * L["scale"]) for L in new)
            for L in new:
                L["y"] += dy

    if texts and subjs and sz:
        text_w = int(safe_w * (text_width_ratio or S("layout.text_width_ratio.safe_zone")))
        edge = S("layout.subject_edge_px")   # 피사체는 안전영역 밖(가장자리)까지 사용 가능
        if text_side == "left":
            ov_src = max([e.get("text_overlap_src_px", 0) for e in subjs] + [0])
            if ov_src:
                # 인물 bbox 왼쪽 위의 빈 공간(노트북 때문에 생김)까지 글자 영역을 넓힌다. 인물 위치(얼굴)는 그대로 둔다.
                # bbox 간격 대신 실제 인물 실루엣 기준 간격을 validate.check_clearance가 검사한다.
                place(subjs, sx0 + text_w + gap, W - edge, "left")
                sub_layers = [L for L in layout["layers"] if L["type"] != "text"]
                for L in sub_layers:
                    L["overlap_ok"] = True
                wide = int(min(L["x"] for L in sub_layers) + ov_src * sub_layers[0]["scale"] - gap - sx0)
                place(texts, sx0, sx0 + max(text_w, min(wide, sx1 - sx0)), "left")
            else:
                place(texts, sx0, sx0 + text_w, "left")
                place(subjs, sx0 + text_w + gap, W - edge, "left")   # 글자 쪽에 붙여 안전영역 안 비율 확보
        else:
            zpad = S("layout.zone_pad_px")   # 안전영역 경계에 딱 붙으면 기기별 반올림 오차로 넘을 수 있어 안쪽으로 띄움
            place(subjs, edge, sx1 - zpad - text_w - gap, "right")   # 글자 쪽으로 붙여 안전영역 안 비율 확보
            place(texts, sx1 - zpad - text_w, sx1 - zpad, "right")
    elif texts and subjs:
        text_w = int(safe_w * S("layout.text_width_ratio.safe_margin"))
        if text_side == "left":
            place(texts, sx0, sx0 + text_w, "left")
            place(subjs, sx0 + text_w + gap, sx1, "right")
        else:
            place(subjs, sx0, sx1 - text_w - gap, "left")
            place(texts, sx1 - text_w, sx1, "right")
    else:
        place(texts or subjs, sx0, sx1, "left")

    if footer:   # 안내 문구는 띠 안에 가운데 정렬, 안전영역 폭에 맞춰 축소
        uf = _union([e["bbox"] for e in footer])
        fw, fh = uf[2] - uf[0], uf[3] - uf[1]
        bp = S("layout.band_text_pad_px")
        region = min(band_h, sy1 - floor_y)   # 띠 안에서도 안전영역 아래 여백을 지킨다
        fsc = min((sx1 - sx0) / fw, (region - 2 * bp) / fh, 1.0)
        fx = sx0 + ((sx1 - sx0) - fw * fsc) / 2
        fy = floor_y + (region - fh * fsc) / 2
        for e in footer:
            b = e["bbox"]
            layout["layers"].append({"id": e["id"], "type": "text", "importance": "critical", "src_bbox": b,
                                     "scale": round(fsc, 4), "x": int(round(fx + (b[0] - uf[0]) * fsc)),
                                     "y": int(round(fy + (b[1] - uf[1]) * fsc)), "group": "footer"})

    if notes:
        un = _union([e["bbox"] for e in notes])
        nx = min([L["x"] for L in layout["layers"] if L["type"] == "text"] + [sx0])
        for e in notes:
            b = e["bbox"]
            layout["layers"].append({"id": e["id"], "type": "text", "importance": "critical", "src_bbox": b,
                                     "scale": 1.0, "x": int(round(nx + b[0] - un[0])),
                                     "y": int(round(sy1 - (un[3] - un[1]) + b[1] - un[1])), "group": "footer"})

    if scrim and texts:
        tl = [L for L in layout["layers"] if L["type"] == "text"]
        bx0 = min(L["x"] for L in tl); by0 = min(L["y"] for L in tl)
        bx1 = max(L["x"] + round((L["src_bbox"][2] - L["src_bbox"][0]) * L["scale"]) for L in tl)
        by1 = max(L["y"] + round((L["src_bbox"][3] - L["src_bbox"][1]) * L["scale"]) for L in tl)
        pad = S("scrim.pad_px")
        layout["scrim"] = {"box": [bx0 - pad, by0 - pad, bx1 + pad, by1 + pad],
                           "alpha": S("scrim.alpha"), "feather": S("scrim.feather_px")}
    return layout


def auto_free(elements, src_size, preset, **_):
    """세로/정사각용 자유 배치. 원본의 요소 배열을 유지한 채 균등 축소하여 우하단 기준으로 앉힘.
    배경은 cover + 원본 오른쪽/아래 기준으로 crop (글자가 원래 놓여 있던 흰 영역을 그대로 활용)."""
    W, H = preset["size"]
    m = preset["safe_margin"]["x"]
    sw, sh = src_size
    crit = [e for e in elements if e["importance"] == "critical"]
    u = _union([e["bbox"] for e in crit])
    uw, uh = u[2] - u[0], u[3] - u[1]
    f = min(1.0, (W - 2 * m) / uw, (H - m) / uh)

    s = max(W / sw, H / sh)
    bx = min(0, W - int(round(sw * s)))            # 오른쪽 기준 crop
    by = min(0, H - int(round(sh * s)))            # 아래 기준 crop
    layout = {"bg": {"scale": s, "x": bx, "y": by}, "layers": []}

    right = W - m
    bottom_bleed = any(e["bbox"][3] >= sh - S("layout.bleed_touch_px") and e["type"] != "text" for e in crit)
    bottom = H if bottom_bleed else H - m
    for e in crit:
        b = e["bbox"]
        layout["layers"].append({
            "id": e["id"], "type": e["type"], "importance": "critical",
            "src_bbox": b, "scale": round(f, 4),
            "x": int(round(right - (u[2] - b[0]) * f)),
            "y": int(round(bottom - (u[3] - b[1]) * f)),
            **({"bleed": ["bottom"]} if bottom_bleed and e["type"] != "text" else {}),
        })
    if bottom_bleed:   # 반올림 오차 보정: 피사체 하단을 캔버스 끝에 정확히
        sub = [L for L in layout["layers"] if "bleed" in L]
        dy = H - max(round(L["y"] + (L["src_bbox"][3] - L["src_bbox"][1]) * L["scale"]) for L in sub)
        for L in layout["layers"]:
            L["y"] += dy
    return layout


def build(elements, src_size, preset, style_dict=None, overrides=None, style_name=None, meta=None):
    """프리셋의 mode/비율에 맞는 배치 함수를 고른다.
    배치 옵션은 스타일 프로필(소재 유형별)에서 오고, 사람이 CLI로 준 덮어쓰기는 layout['overrides']에 기록한다."""
    W, H = preset["size"]
    opts = _spec.resolve_options(style_dict or {}, preset, src_size, overrides)
    if preset.get("mode") == "free" and H / W >= 1.6:
        lay = auto_stack(elements, src_size, preset, **preset.get("stack", {}))
    elif preset.get("mode") == "free" and W / H < 1.3:
        lay = auto_free(elements, src_size, preset)
    else:
        lay = auto_gfa(elements, src_size, preset, opts["text_side"], bg_top=opts["bg_top"],
                       text_scale=opts["text_scale"], person_h=opts["person_h"], scrim=opts["scrim"],
                       text_width_ratio=opts["text_width_ratio"], text_v_ratio=opts["text_v_ratio"])
    lay = place_movable(lay, elements, preset)
    meta = meta or {}
    if meta.get("bg_model"):   # flat 배경: crop 대신 색 모델로 다시 그림
        lay["bg"]["mode"] = "flat"
        lay["bg"]["model"] = meta["bg_model"]
        if lay.get("band"):
            bm = (meta.get("band_models") or {}).get(next(e["id"] for e in elements if e["importance"] == "band"))
            if bm:
                lay["band"]["model"] = bm
        lay = place_decor(lay, elements, preset, src_size)
    if style_name:
        lay["style"] = style_name
    given = {k: v for k, v in (overrides or {}).items() if v is not None}
    if given:
        lay["overrides"] = given   # 스타일에서 벗어난 수동 조정은 기록해 보고서에 경고로 드러낸다
    return lay


def auto_stack(elements, src_size, preset, text_top=700, text_up=1.4, person_up=1.1, **_):
    """초세로(9:16)용 수직 스택. 배경은 폭 기준으로 전체를 위에 두고(잘림 없음) 아래는 LaMa로 이어 채움.
    글자 블록은 중앙, 피사체는 좌하단 bleed. 어떤 요소도 crop하지 않는다.
    띠(importance=band)가 있으면: 피사체는 띠 윗면에 서고, group=footer 문구는 띠 안에 가운데 정렬한다."""
    W, H = preset["size"]
    m = preset["safe_margin"]["x"]
    gap = preset["min_gap"]
    sw, sh = src_size
    crit = [e for e in elements if e["importance"] == "critical"]
    band_el = next((e for e in elements if e["importance"] == "band"), None)
    all_texts = [e for e in crit if e["type"] == "text"]
    footer = [e for e in all_texts if e.get("group") == "footer"] if band_el else []
    texts = [e for e in all_texts if e not in footer]
    subjs = [e for e in crit if e["type"] != "text"]
    s = W / sw
    layout = {"bg": {"scale": s, "x": 0, "y": 0}, "layers": []}
    band_h = round((band_el["bbox"][3] - band_el["bbox"][1]) * s) if band_el else 0
    floor_y = H - band_h
    if band_el:
        layout["band"] = {"src_bbox": band_el["bbox"], "h": band_h}
        layout["floor"] = {"y": floor_y, "src_y": band_el["bbox"][1]}

    ut = _union([e["bbox"] for e in texts])
    ft = min(text_up, (W - 2 * m) / (ut[2] - ut[0]))
    text_h = (ut[3] - ut[1]) * ft
    fp_band = None
    if band_el and subjs:   # 띠가 있으면 인물 크기를 먼저 정하고, 글자는 그 위에 쌓는다
        up0 = _union([e["bbox"] for e in subjs])
        fp_band = min(person_up, (W - 2 * m) / (up0[2] - up0[0]), 0.4 * H / (up0[3] - up0[1]))
        text_top = min(text_top, floor_y - (up0[3] - up0[1]) * fp_band - gap - text_h)
    t_bottom = text_top + text_h
    for e in texts:
        b = e["bbox"]
        layout["layers"].append({
            "id": e["id"], "type": "text", "importance": "critical", "src_bbox": b, "scale": round(ft, 4),
            "x": int(round((W - (ut[2] - ut[0]) * ft) / 2 + (b[0] - ut[0]) * ft)),
            "y": int(round(text_top + (b[1] - ut[1]) * ft))})

    if subjs:
        up = _union([e["bbox"] for e in subjs])
        uw, uh = up[2] - up[0], up[3] - up[1]
        on_band = bool(band_el) and any(e["bbox"][3] >= band_el["bbox"][1] - 3 for e in subjs)
        bleed = (not on_band) and any(e["bbox"][3] >= sh - S("layout.bleed_touch_px") for e in subjs)
        base = floor_y if on_band else H
        fp = min(person_up, (W - 2 * m) / uw, (base - t_bottom - gap) / uh)   # 글자와 겹치지 않게
        top = (base - uh * fp) if (bleed or on_band) else (H - m - uh * fp)
        start = len(layout["layers"])
        for e in subjs:
            b = e["bbox"]
            layout["layers"].append({
                "id": e["id"], "type": e["type"], "importance": "critical", "src_bbox": b, "scale": round(fp, 4),
                "x": int(round(m + (b[0] - up[0]) * fp)), "y": int(round(top + (b[1] - up[1]) * fp)),
                **({"bleed": ["bottom"]} if bleed else {}), **({"on_band": True} if on_band else {})})
        if bleed:
            new = layout["layers"][start:]
            dy = H - max(round(L["y"] + (L["src_bbox"][3] - L["src_bbox"][1]) * L["scale"]) for L in new)
            for L in new:
                L["y"] += dy

    if footer:   # 안내 문구: 띠 안에 가운데 정렬
        uf = _union([e["bbox"] for e in footer])
        fw, fh = uf[2] - uf[0], uf[3] - uf[1]
        bp = S("layout.band_text_pad_px")
        region = min(band_h, H - m - floor_y)   # 띠 안에서도 아래 여백을 지킨다
        fsc = min((W - 2 * m) / fw, (region - 2 * bp) / fh, 1.0)
        fx = (W - fw * fsc) / 2
        fy = floor_y + (region - fh * fsc) / 2
        for e in footer:
            b = e["bbox"]
            layout["layers"].append({"id": e["id"], "type": "text", "importance": "critical", "src_bbox": b,
                                     "scale": round(fsc, 4), "x": int(round(fx + (b[0] - uf[0]) * fsc)),
                                     "y": int(round(fy + (b[1] - uf[1]) * fsc)), "group": "footer"})
    return layout


def _overlap(a, b, pad=0):
    return not (a[2] + pad <= b[0] or b[2] + pad <= a[0] or a[3] + pad <= b[1] or b[3] + pad <= a[1])


def place_movable(layout, elements, preset, decor_scale=None, gap=None, margin=None):
    """movable(재배치 가능한 장식) 배치. 배경과 같은 위치(in_place)에 보이고 필수요소와 안 겹치면 그대로,
    아니면 가장 가까운 빈자리로 이동(relocated). 자리가 없으면 레이어를 만들지 않음 -> 인벤토리에서 LOST로 보고."""
    decor_scale = S("movable.decor_scale") if decor_scale is None else decor_scale
    gap = S("movable.gap_px") if gap is None else gap
    margin = S("movable.margin_px") if margin is None else margin
    grid = S("movable.grid_step_px")
    W, H = preset["size"]
    bg = layout["bg"]
    s, bx, by = bg["scale"], bg["x"], bg["y"]
    taken = []
    for L in layout["layers"]:
        x0, y0, x1, y1 = L["src_bbox"]
        taken.append([L["x"], L["y"], L["x"] + round((x1 - x0) * L["scale"]), L["y"] + round((y1 - y0) * L["scale"])])
    mov = [e for e in elements if e["importance"] == "movable"]
    mov.sort(key=lambda e: -((e["bbox"][2] - e["bbox"][0]) * (e["bbox"][3] - e["bbox"][1])))
    for e in mov:
        x0, y0, x1, y1 = e["bbox"]
        w, h = x1 - x0, y1 - y0
        mx, my = round(x0 * s + bx), round(y0 * s + by)
        box = [mx, my, mx + round(w * s), my + round(h * s)]
        inside = box[0] >= 0 and box[1] >= 0 and box[2] <= W and box[3] <= H
        if inside and not any(_overlap(box, t, gap) for t in taken):
            pos, sc, mode = (mx, my), s, "in_place"
        else:
            pos = None
            for sc in [min(s, decor_scale) * k for k in S("movable.scale_steps")]:
                wi, hi = round(w * sc), round(h * sc)
                tx = min(max(mx, margin), W - margin - wi)
                ty = min(max(my, margin), H - margin - hi)
                best = None
                # stay_y: 받침대 위 소품처럼 높이가 의미 있는 요소는 가로로만 이동 (허공에 뜨는 것 방지)
                ys = [min(max(my, margin), H - margin - hi)] if e.get("stay_y") else range(margin, H - margin - hi + 1, grid)
                for yy in ys:
                    for xx in range(margin, W - margin - wi + 1, grid):
                        c = [xx, yy, xx + wi, yy + hi]
                        if any(_overlap(c, t, gap) for t in taken):
                            continue
                        d = (xx - tx) ** 2 + (yy - ty) ** 2
                        if best is None or d < best[0]:
                            best = (d, xx, yy)
                if best:
                    pos, mode = (best[1], best[2]), "relocated"
                    break
        if pos is None:
            continue
        wi, hi = round(w * sc), round(h * sc)
        taken.append([pos[0], pos[1], pos[0] + wi, pos[1] + hi])
        layout["layers"].append({"id": e["id"], "type": "decor", "importance": "movable", "src_bbox": e["bbox"],
                                 "scale": round(sc, 4), "x": pos[0], "y": pos[1], "placement": mode})
    return layout


def place_decor(layout, elements, preset, src_size):
    """장식(importance=decor)을 앵커 기준으로 배치. 원본에서 가장 가까운 모서리까지의 거리를 캔버스 높이 비율로 옮긴다.
    모서리 앵커는 캔버스 밖으로 걸쳐도 된다(장식은 안전영역 밖 허용). 글자와 겹치면 줄이고, 그래도 겹치면 optional만 뺀다.
    결과는 레이어의 placement/scale 또는 layout['dropped'](사유 포함)에 남겨 인벤토리가 보고한다."""
    W, H = preset["size"]
    sw, sh = src_size
    floor = layout.get("floor", {"y": H, "src_y": sh})
    # 원본의 세로 높이(바닥=띠 윗면까지)를 캔버스 바닥 위에 그대로 담는 비율. 장식끼리 원본의 위아래 배치가 유지된다.
    k = floor["y"] / floor["src_y"]
    gap = S("decor.gap_px")
    texts = [L for L in layout["layers"] if L["type"] == "text"]
    subjects = [L for L in layout["layers"] if L["type"] not in ("text", "decor")]

    def box(L):
        x0, y0, x1, y1 = L["src_bbox"]
        return [L["x"], L["y"], L["x"] + round((x1 - x0) * L["scale"]), L["y"] + round((y1 - y0) * L["scale"])]

    blockers_text = [box(L) for L in texts if L.get("group") != "footer"]   # 띠 안 문구는 띠 위 장식과 다른 층
    blockers_subj = [box(L) for L in subjects]
    layout.setdefault("dropped", [])
    z_of = {"back": -2, "behind_subject": -1, "front": 1}
    for e in [e for e in elements if e["importance"] == "decor"]:
        x0, y0, x1, y1 = e["bbox"]
        anchor = e.get("anchor", "corner_tl")
        overlap = e.get("overlap", "none")          # any | subject | none
        placed = None
        for f in S("decor.shrink_steps"):
            sc = k * f
            w, h = round((x1 - x0) * sc), round((y1 - y0) * sc)
            # 앵커 모서리를 기준으로 원본의 모서리 거리를 비율대로 유지(축소해도 모서리 쪽은 고정)
            left = "l" in anchor.split("_")[-1]
            top = "t" in anchor.split("_")[-1]
            nx = round(x0 * k) if left else round(W - (sw - x1) * k) - w
            ny = round(y0 * k) if top else round(floor["y"] - (floor["src_y"] - y1) * k) - h
            b = [nx, ny, nx + w, ny + h]
            blockers = [] if overlap == "any" else (blockers_text if overlap == "subject" else blockers_text + blockers_subj)
            hit = any(not (b[2] + gap <= o[0] or o[2] + gap <= b[0] or b[3] + gap <= o[1] or o[3] + gap <= b[1]) for o in blockers)
            if not hit:
                placed = (nx, ny, sc, f)
                break
        if placed is None:
            if e.get("priority", "optional") == "optional":
                layout["dropped"].append({"id": e["id"], "reason": "글자/인물과 겹쳐 자리 없음(최소 배율까지 축소해도)"})
                continue
            nx, ny, sc, f = nx, ny, sc, f   # keep: 마지막 축소 상태로라도 둔다(검사에서 겹침이 드러남)
            placed = (nx, ny, sc, f)
        nx, ny, sc, f = placed
        layout["layers"].append({"id": e["id"], "type": "decor", "importance": "decor", "src_bbox": e["bbox"],
                                 "scale": round(sc, 4), "x": nx, "y": ny, "anchor": anchor,
                                 "placement": "anchored" if f == 1.0 else f"anchored_shrunk_{f}",
                                 "z": z_of.get(e.get("z", "back"), -2), "bleed_ok": True})
    return layout
