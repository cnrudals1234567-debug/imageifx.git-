"""GFA 모드 자동 배치. 결과는 정수 px 좌표의 layout.json (이후 사람이 px 단위로 수정)."""


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _align_dx(texts, tol=0.10):
    """중앙 정렬 디자인에서 중심이 크게 어긋난 줄만 가운데로 당길 이동량(원본 px)을 돌려줌.
    좌측 정렬 디자인(왼쪽 끝이 더 가지런함)은 건드리지 않는다."""
    import statistics as st
    if len(texts) < 3:
        return {}
    cs = [(e["bbox"][0] + e["bbox"][2]) / 2 for e in texts]
    ls = [e["bbox"][0] for e in texts]
    if st.pstdev(cs) >= st.pstdev(ls):
        return {}
    med = st.median(cs)
    uw = max(e["bbox"][2] for e in texts) - min(e["bbox"][0] for e in texts)
    return {e["id"]: med - c for e, c in zip(texts, cs) if abs(c - med) > tol * uw}


def auto_gfa(elements, src_size, preset, text_side="left", bg_top=None,
             text_scale=1.0, person_h=None, scrim=False):
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
    texts = [e for e in crit if e["type"] == "text"]
    subjs = [e for e in crit if e["type"] != "text"]

    # 배경: cover 방식 (캔버스를 가득 채움 -> 확장 불필요), 세로는 필수요소 중심으로 crop
    s = max(W / sw, H / sh)
    cy = (sum((e["bbox"][1] + e["bbox"][3]) / 2 for e in crit) / len(crit)) if crit else sh / 2
    by = int(round(H / 2 - cy * s)) if bg_top is None else -int(round(bg_top * s))
    by = min(0, max(H - int(round(sh * s)), by))
    bx = int(round((W - sw * s) / 2))
    layout = {"bg": {"scale": s, "x": bx, "y": by}, "layers": []}

    def place(group, x_left, x_right, align):
        """group을 [x_left,x_right] 안에 세로 중앙 정렬로 배치. 균등 축소만 사용."""
        if not group:
            return
        dxs = _align_dx(group) if group[0]["type"] == "text" else {}
        u = _union([[e["bbox"][0] + dxs.get(e["id"], 0), e["bbox"][1], e["bbox"][2] + dxs.get(e["id"], 0), e["bbox"][3]] for e in group])
        uw, uh = u[2] - u[0], u[3] - u[1]
        # 원본 아래 끝에 걸려 있던 피사체는 직선으로 잘려 보이지 않게 캔버스 하단으로 bleed
        bleed_b = group[0]["type"] != "text" and any(e["bbox"][3] >= sh - 2 for e in group)
        avail_h = (H - sy0) if bleed_b else safe_h
        is_text = group[0]["type"] == "text"
        cap = text_scale if is_text else ((person_h / uh) if person_h else 99)
        sc = min((x_right - x_left) / uw, avail_h / uh, cap)
        pw, ph = uw * sc, uh * sc
        ox = x_left if align == "left" else x_right - pw
        oy = (H - ph) if bleed_b else sy0 + (safe_h - ph) / 2
        start = len(layout["layers"])
        for e in group:
            b = e["bbox"]
            layout["layers"].append({
                "id": e["id"], "type": e["type"], "importance": "critical",
                "src_bbox": b, "scale": round(sc, 4),
                "x": int(round(ox + (b[0] + dxs.get(e["id"], 0) - u[0]) * sc)),
                "y": int(round(oy + (b[1] - u[1]) * sc)),
                **({"bleed": ["bottom"]} if bleed_b else {}),
            })
        if bleed_b:  # 반올림 오차로 1px 틈이 생기지 않게 하단을 캔버스 끝에 정확히 맞춤
            new = layout["layers"][start:]
            dy = H - max(round(L["y"] + (L["src_bbox"][3] - L["src_bbox"][1]) * L["scale"]) for L in new)
            for L in new:
                L["y"] += dy

    if texts and subjs and sz:
        text_w = int(safe_w * 0.62)
        edge = 24   # 피사체는 안전영역 밖(가장자리)까지 사용 가능
        if text_side == "left":
            place(texts, sx0, sx0 + text_w, "left")
            place(subjs, sx0 + text_w + gap, W - edge, "right")
        else:
            zpad = 16   # 안전영역 경계에 딱 붙으면 기기별 반올림 오차로 넘을 수 있어 안쪽으로 띄움
            place(subjs, edge, sx1 - zpad - text_w - gap, "right")   # 글자 쪽으로 붙여 안전영역 안 비율 확보
            place(texts, sx1 - zpad - text_w, sx1 - zpad, "right")
    elif texts and subjs:
        text_w = int(safe_w * 0.48)
        if text_side == "left":
            place(texts, sx0, sx0 + text_w, "left")
            place(subjs, sx0 + text_w + gap, sx1, "right")
        else:
            place(subjs, sx0, sx1 - text_w - gap, "left")
            place(texts, sx1 - text_w, sx1, "right")
    else:
        place(texts or subjs, sx0, sx1, "left")

    if scrim and texts:
        tl = [L for L in layout["layers"] if L["type"] == "text"]
        bx0 = min(L["x"] for L in tl); by0 = min(L["y"] for L in tl)
        bx1 = max(L["x"] + round((L["src_bbox"][2] - L["src_bbox"][0]) * L["scale"]) for L in tl)
        by1 = max(L["y"] + round((L["src_bbox"][3] - L["src_bbox"][1]) * L["scale"]) for L in tl)
        pad = 40
        layout["scrim"] = {"box": [bx0 - pad, by0 - pad, bx1 + pad, by1 + pad], "alpha": 0.95, "feather": 36}
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
    bottom_bleed = any(e["bbox"][3] >= sh - 2 and e["type"] != "text" for e in crit)
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


def build(elements, src_size, preset, text_side="right", **opts):
    """프리셋의 mode/비율에 맞는 배치 함수 선택. opts가 None이면 프리셋 defaults 사용."""
    W, H = preset["size"]
    merged = {**preset.get("defaults", {}), **{k: v for k, v in opts.items() if v not in (None, False)}}
    if merged.get("scrim") == "off":
        merged["scrim"] = False
    if preset.get("mode") == "free" and H / W >= 1.6:
        lay = auto_stack(elements, src_size, preset, **preset.get("defaults", {}))
    elif preset.get("mode") == "free" and W / H < 1.3:
        lay = auto_free(elements, src_size, preset)
    else:
        lay = auto_gfa(elements, src_size, preset, text_side, **merged)
    return place_movable(lay, elements, preset)


def auto_stack(elements, src_size, preset, text_top=700, text_up=1.4, person_up=1.1, **_):
    """초세로(9:16)용 수직 스택. 배경은 폭 기준으로 전체를 위에 두고(잘림 없음) 아래는 LaMa로 이어 채움.
    글자 블록은 중앙, 피사체는 좌하단 bleed. 어떤 요소도 crop하지 않는다."""
    W, H = preset["size"]
    m = preset["safe_margin"]["x"]
    gap = preset["min_gap"]
    sw, sh = src_size
    crit = [e for e in elements if e["importance"] == "critical"]
    texts = [e for e in crit if e["type"] == "text"]
    subjs = [e for e in crit if e["type"] != "text"]
    s = W / sw
    layout = {"bg": {"scale": s, "x": 0, "y": 0}, "layers": []}

    ut = _union([e["bbox"] for e in texts])
    ft = min(text_up, (W - 2 * m) / (ut[2] - ut[0]))
    t_bottom = text_top + (ut[3] - ut[1]) * ft
    for e in texts:
        b = e["bbox"]
        layout["layers"].append({
            "id": e["id"], "type": "text", "importance": "critical", "src_bbox": b, "scale": round(ft, 4),
            "x": int(round((W - (ut[2] - ut[0]) * ft) / 2 + (b[0] - ut[0]) * ft)),
            "y": int(round(text_top + (b[1] - ut[1]) * ft))})

    if subjs:
        up = _union([e["bbox"] for e in subjs])
        uw, uh = up[2] - up[0], up[3] - up[1]
        bleed = any(e["bbox"][3] >= sh - 2 for e in subjs)
        fp = min(person_up, (W - 2 * m) / uw, (H - t_bottom - gap) / uh)   # 글자와 겹치지 않게
        top = (H - uh * fp) if bleed else (H - m - uh * fp)
        start = len(layout["layers"])
        for e in subjs:
            b = e["bbox"]
            layout["layers"].append({
                "id": e["id"], "type": e["type"], "importance": "critical", "src_bbox": b, "scale": round(fp, 4),
                "x": int(round(m + (b[0] - up[0]) * fp)), "y": int(round(top + (b[1] - up[1]) * fp)),
                **({"bleed": ["bottom"]} if bleed else {})})
        if bleed:
            new = layout["layers"][start:]
            dy = H - max(round(L["y"] + (L["src_bbox"][3] - L["src_bbox"][1]) * L["scale"]) for L in new)
            for L in new:
                L["y"] += dy
    return layout


def _overlap(a, b, pad=0):
    return not (a[2] + pad <= b[0] or b[2] + pad <= a[0] or a[3] + pad <= b[1] or b[3] + pad <= a[1])


def place_movable(layout, elements, preset, decor_scale=0.65, gap=28, margin=12):
    """movable(재배치 가능한 장식) 배치. 배경과 같은 위치(in_place)에 보이고 필수요소와 안 겹치면 그대로,
    아니면 가장 가까운 빈자리로 이동(relocated). 자리가 없으면 레이어를 만들지 않음 -> 인벤토리에서 LOST로 보고."""
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
            for sc in (min(s, decor_scale), min(s, decor_scale) * 0.8, min(s, decor_scale) * 0.65):
                wi, hi = round(w * sc), round(h * sc)
                tx = min(max(mx, margin), W - margin - wi)
                ty = min(max(my, margin), H - margin - hi)
                best = None
                # stay_y: 받침대 위 소품처럼 높이가 의미 있는 요소는 가로로만 이동 (허공에 뜨는 것 방지)
                ys = [min(max(my, margin), H - margin - hi)] if e.get("stay_y") else range(margin, H - margin - hi + 1, 8)
                for yy in ys:
                    for xx in range(margin, W - margin - wi + 1, 8):
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
