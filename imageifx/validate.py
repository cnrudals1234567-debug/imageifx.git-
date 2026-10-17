"""GFA 규칙 검수. LLM이 아니라 코드가 최종 판정."""


def _box(L):
    x0, y0, x1, y1 = L["src_bbox"]
    return [L["x"], L["y"], L["x"] + round((x1 - x0) * L["scale"]), L["y"] + round((y1 - y0) * L["scale"])]


def _zone(preset):
    """안전영역 (left, top, right, bottom) px. safe_zone이 있으면 방향별 값, 없으면 safe_margin 대칭값."""
    W, H = preset["size"]
    z = preset.get("safe_zone")
    if z:
        return z["left"], z["top"], W - z["right"], H - z["bottom"]
    mx, my = preset["safe_margin"]["x"], preset["safe_margin"]["y"]
    return mx, my, W - mx, H - my


def _src_touch(a, c):
    """두 레이어의 원본 bbox가 서로 겹치는가(원본에서 한 덩어리로 놓여 있었는가)."""
    A, B = a["src_bbox"], c["src_bbox"]
    return not (A[2] <= B[0] or B[2] <= A[0] or A[3] <= B[1] or B[3] <= A[1])


def check(layout, preset, out_size=None, file_kb=None):
    W, H = preset["size"]
    zl, zt, zr, zb = _zone(preset)
    res = []

    def add(name, ok, detail=""):
        res.append({"check": name, "ok": bool(ok), "detail": detail})

    if out_size is not None:
        add("출력 픽셀 크기", tuple(out_size) == (W, H), f"{out_size} / 목표 {(W, H)}")
    if file_kb is not None and preset.get("max_kb"):
        add("파일 용량 상한", file_kb < preset["max_kb"], f"{file_kb:.0f}KB / 최대 {preset['max_kb']}KB (1KB=1000B 기준)")
    if file_kb is not None and preset.get("min_kb"):
        add("파일 용량 하한", file_kb >= preset["min_kb"], f"{file_kb:.0f}KB / 최소 {preset['min_kb']}KB (미만이면 저화질로 등록 차단)")

    crit = [L for L in layout["layers"] if L["importance"] == "critical"]
    for L in crit:
        b = _box(L)
        bl = L.get("bleed", [])
        if L["type"] == "text":
            # 글자·로고·CTA: 안전영역 안이 필수 (기기별 좌우 잘림 때문에 반려되는 핵심 규칙)
            ok = b[0] >= zl and b[1] >= zt and b[2] <= zr and b[3] <= zb
            add(f"안전영역 글자 [{L['id']}]", ok, f"bbox={b}, 안전영역=({zl},{zt})~({zr},{zb}) [{zr-zl}x{zb-zt}]")
        else:
            # 피사체: 캔버스 안이면 필수, 안전영역 밖은 정보로 표시 (일부 디자인 요소는 밖에 있어도 됨)
            hi_y = H if "bottom" in bl else H
            ok = b[0] >= 0 and b[1] >= 0 and b[2] <= W and b[3] <= hi_y
            add(f"캔버스 안 [{L['id']}]", ok, f"bbox={b}")
            iw = max(0, min(b[2], zr) - max(b[0], zl))
            ih = max(0, min(b[3], zb) - max(b[1], zt))
            share = iw * ih / max(1, (b[2] - b[0]) * (b[3] - b[1]))
            if share < 0.999:
                res.append({"check": f"안전영역 안 비율 [{L['id']}]", "ok": True, "warn": True,
                            "detail": f"{share:.0%} (좌우 가장자리는 기기에 따라 잘릴 수 있음 - 얼굴/핵심 부분이 밖에 있지 않은지 눈으로 확인)"})
        if "bottom" in bl:
            add(f"bleed 하단 접촉 [{L['id']}]", b[3] >= H, f"하단={b[3]} / 캔버스 {H}")
    for L in crit:
        if L["type"] == "text":
            h = (L.get("text_height") or (L["src_bbox"][3] - L["src_bbox"][1])) * L["scale"]
            add(f"최소 글자 높이 [{L['id']}]", h >= preset["min_text_height"], f"{h:.0f}px / 최소 {preset['min_text_height']}px")
    gap = preset["min_gap"]
    for i, a in enumerate(crit):
        for c in crit[i + 1:]:
            A, B = _box(a), _box(c)
            dx = max(B[0] - A[2], A[0] - B[2])
            dy = max(B[1] - A[3], A[1] - B[3])
            d = max(dx, dy)   # 음수면 겹침
            if (a.get("overlap_ok") and c["type"] == "text") or (c.get("overlap_ok") and a["type"] == "text"):
                continue   # bbox가 겹쳐도 되는 인물: 간격은 실루엣 기준(check_clearance)으로 검사
            if (a.get("on_band") and c.get("group") == "footer") or (c.get("on_band") and a.get("group") == "footer"):
                continue   # 인물은 띠 윗면에 서 있고 안내 문구는 띠 안에 있어 서로 다른 층이다
            if a["type"] != "text" and c["type"] != "text" and a["scale"] == c["scale"] and _src_touch(a, c):
                continue   # 원본에서 이미 겹쳐 있던 피사체(인물+노트북 등)는 같은 배율이면 그 관계가 그대로 유지된다(새로 가까워진 것이 아님)
            if a["type"] == c["type"] == "text":
                # 글자 블록끼리의 간격은 원본 디자인의 줄 간격을 축소한 값이라 실패로 단정하지 않고 정보로만 표시
                if d < gap:
                    res.append({"check": f"글자 블록 간격 [{a['id']}↔{c['id']}]", "ok": True, "warn": True,
                                "detail": f"{d}px (원본 간격 비율 유지, 임시 기준 {gap}px 미만 - 실제 GFA 규정 확인 필요)"})
                continue
            add(f"간격 [{a['id']}↔{c['id']}]", d >= gap, f"{d}px / 최소 {gap}px")
    return res


def inventory(elements, layout, preset):
    """원본의 모든 요소가 결과에 살아 있는지 요소별로 점검. 규칙 검사가 못 잡던 '조용한 누락'을 잡는다."""
    W, H = preset["size"]
    bg = layout["bg"]
    s, bx, by = bg["scale"], bg["x"], bg["y"]
    by_id = {L["id"]: L for L in layout["layers"]}
    boxes = [_box(L) for L in layout["layers"]]
    rows = []

    def vis(b):
        iw = max(0, min(b[2], W) - max(b[0], 0))
        ih = max(0, min(b[3], H) - max(b[1], 0))
        return (iw * ih) / max(1, (b[2] - b[0]) * (b[3] - b[1]))

    for e in elements:
        imp, eid = e["importance"], e["id"]
        if imp == "drop":
            rows.append({"id": eid, "kind": "drop", "status": "DROPPED", "ok": True, "warn": True, "detail": "의도적으로 제거됨"})
        elif imp == "decor":
            L = by_id.get(eid)
            drop = next((d for d in layout.get("dropped", []) if d["id"] == eid), None)
            if L:
                st = "OK/" + L.get("placement", "anchored")
                rows.append({"id": eid, "kind": "decor", "status": st, "ok": True, "warn": "shrunk" in st,
                             "detail": f"앵커 {L.get('anchor')} (캔버스 밖 걸침 허용)"})
            elif drop:
                rows.append({"id": eid, "kind": "decor", "status": "DROPPED(optional)", "ok": True, "warn": True, "detail": drop["reason"]})
            else:
                rows.append({"id": eid, "kind": "decor", "status": "LOST", "ok": False,
                             "detail": "배치되지 않음(flat 배경이 아니면 decor 복사가 동작하지 않음)"})
        elif imp == "band":
            ok = bool(layout.get("band"))
            rows.append({"id": eid, "kind": "band", "status": "OK" if ok else "LOST", "ok": ok,
                         "detail": "하단 띠를 결과 아래에 다시 붙임" if ok else "띠가 결과에 없음"})
        elif imp in ("critical", "movable"):
            L = by_id.get(eid)
            if not L:
                if imp == "movable" and e.get("priority") == "optional":   # 작은 소품: 자리가 없으면 경고로만 보고(조용한 손실이 아님)
                    rows.append({"id": eid, "kind": imp, "status": "DROPPED(optional)", "ok": True, "warn": True,
                                 "detail": "배치할 자리가 없어 뺌(optional 소품)"})
                else:
                    rows.append({"id": eid, "kind": imp, "status": "LOST", "ok": False, "detail": "배치할 자리가 없어 누락"})
                continue
            v = vis(_box(L))
            bl = L.get("bleed", [])
            full = v >= 0.999 or (bool(bl) and v >= 0.97)
            st = ("OK" if full else "CUT") + (("/" + L["placement"]) if "placement" in L else "")
            rows.append({"id": eid, "kind": imp, "status": st, "ok": bool(full),
                         "warn": L.get("placement") == "relocated", "detail": f"보이는 비율 {v:.0%}"})
        else:  # 배경에 남는 장식
            b = [round(e["bbox"][0] * s + bx), round(e["bbox"][1] * s + by),
                 round(e["bbox"][2] * s + bx), round(e["bbox"][3] * s + by)]
            v = vis(b)
            area = max(1, (b[2] - b[0]) * (b[3] - b[1]))
            occ = max([0] + [max(0, min(b[2], c[2]) - max(b[0], c[0])) * max(0, min(b[3], c[3]) - max(b[1], c[1])) / area
                             for c in boxes])
            st = "LOST" if v < 0.05 else ("CUT" if v < 0.9 else ("HIDDEN" if occ > 0.3 else "OK"))
            rows.append({"id": eid, "kind": "decor", "status": st, "ok": st == "OK", "warn": st != "OK",
                         "detail": f"보이는 비율 {v:.0%}, 다른 요소에 가려진 비율 {occ:.0%}"})
    return rows



def _text_layers_scaled(layers, layout):
    """글자 레이어를 출력 크기로 축소한 RGBA 배열 목록 [(id, rgba, x, y)]."""
    import numpy as np
    from PIL import Image
    out = []
    for L in layout["layers"]:
        if L["type"] != "text" or L["id"] not in layers:
            continue
        im = layers[L["id"]]
        w, h = max(1, round(im.width * L["scale"])), max(1, round(im.height * L["scale"]))
        out.append((L["id"], np.asarray(im.resize((w, h), Image.LANCZOS)), L["x"], L["y"]))
    return out


def _segments(mask_rows, min_gap=6):
    """True인 행들을 연속 구간(줄)으로 묶음. 빈 행이 min_gap 이상이면 줄이 나뉜 것으로 본다."""
    segs, start, last = [], None, None
    for i, v in enumerate(mask_rows):
        if v:
            if start is None:
                start = i
            elif i - last > min_gap:
                segs.append((start, last))
                start = i
            last = i
    if start is not None:
        segs.append((start, last))
    return segs


def check_image(img, layout, preset, layers):
    """출력 이미지 자체를 보고 판정하는 규칙들 (흰색 면적, 배경색, 가장자리 채움, 글자 크기/줄/색/비율).
    hard 규칙은 FAIL, 단일 출처/실무 기준 규칙은 WARN(ok=True, warn=True)로 구분한다."""
    import numpy as np
    a = np.asarray(img.convert("RGB")).astype(int)
    H, W = a.shape[:2]
    rows = []

    def add(name, ok, detail, hard=True):
        rows.append({"check": name, "ok": bool(ok) or not hard, "warn": (not ok) and not hard, "detail": detail})

    if preset.get("white_max") is not None:
        r = float((a.min(axis=2) >= 245).mean())
        add("흰색 면적", r <= preset["white_max"], f"{r:.0%} / 최대 {preset['white_max']:.0%}")
    if preset.get("forbidden_bg"):
        t = np.array([int(preset["forbidden_bg"][i:i + 2], 16) for i in (1, 3, 5)])
        ring = np.concatenate([a[:20].reshape(-1, 3), a[-20:].reshape(-1, 3), a[:, :20].reshape(-1, 3), a[:, -20:].reshape(-1, 3)])
        med = np.median(ring, axis=0)
        near = bool((np.abs(med - t) <= 6).all())
        add("배경색 금지(#F2F2F2 계열)", not near, f"가장자리 중앙값 RGB{tuple(int(x) for x in med)}")
    if preset.get("no_edge_fill"):
        bad = []
        for name, strip in (("상", a[:4]), ("하", a[-4:]), ("좌", a[:, :4].transpose(1, 0, 2)), ("우", a[:, -4:].transpose(1, 0, 2))):
            white = (strip.min(axis=2) >= 250).mean()
            black = (strip.max(axis=2) <= 5).mean()
            if white > 0.9 or black > 0.9:
                bad.append(f"{name}({'흰색' if white > 0.9 else '검정'} {max(white, black):.0%})")
        add("가장자리 흰색/검정 채움 금지", not bad, ", ".join(bad) if bad else "해당 없음")

    tl = _text_layers_scaled(layers, layout)
    if tl:
        cover = 0
        heights, nlines, px = [], 0, []
        for tid, arr, x, y in tl:
            al = arr[..., 3] > 128
            cover += int(al.sum())
            segs = _segments(al.any(axis=1))
            nlines += len(segs)
            heights += [(tid, e - s_ + 1) for s_, e in segs]
            px.append(arr[..., :3][arr[..., 3] > 200])
        if preset.get("text_ratio_warn") is not None:
            r = cover / (W * H)
            add("텍스트 면적 비율", r <= preset["text_ratio_warn"],
                f"{r:.1%} / 권장 {preset['text_ratio_warn']:.0%} 이내 (실무 기준, 로고·CTA 포함)", hard=False)
        if preset.get("text_height_range"):
            lo, hi = preset["text_height_range"]
            out = [f"{t}:{h}px" for t, h in heights if h < lo or h > hi]
            add("글자 높이 22~52px", not out, ("범위 밖 줄 - " + ", ".join(out)) if out else "모든 줄 범위 안", hard=False)
        if preset.get("max_text_lines"):
            add("글자 줄 수", nlines <= preset["max_text_lines"], f"{nlines}줄 / 최대 {preset['max_text_lines']}줄 (밑줄 등 장식선이 줄로 잡힐 수 있음)", hard=False)
        if preset.get("max_text_colors") and px:
            allp = np.concatenate(px) >> 6
            keys = allp[:, 0] * 16 + allp[:, 1] * 4 + allp[:, 2]
            cnt = np.bincount(keys, minlength=64)
            n = int((cnt >= 0.05 * keys.size).sum())
            add("글자 색 수", n <= preset["max_text_colors"], f"약 {n}색 / 최대 {preset['max_text_colors']}색 (근사 계산)", hard=False)
    return rows



def verify_file(path, preset):
    """저장된 최종 파일만 보고 판정(layout.json을 보지 않음). 에이전트의 독립 2회차 검토용."""
    import os
    import numpy as np
    from PIL import Image
    rows = []

    def add(name, ok, detail):
        rows.append({"check": name, "ok": bool(ok), "detail": detail})

    im = Image.open(path)
    W, H = preset["size"]
    fmt = (im.format or "").upper()
    add("형식", fmt in [f.replace("JPEG", "JPG") for f in preset.get("formats", ["JPG", "PNG"])] or (fmt == "JPEG"), f"{im.format}")
    add("투명 채널 없음", im.mode in ("RGB", "L", "P") and "transparency" not in im.info, f"mode={im.mode}")
    add("픽셀 크기", im.size == (W, H), f"{im.size} / 목표 {(W, H)}")
    n = os.path.getsize(path)
    kb = n / 1000
    add("용량 상한", kb < preset["max_kb"], f"{n}B = {kb:.1f}KB / 최대 {preset['max_kb']}KB (1KB=1000B)")
    if preset.get("min_kb"):
        add("용량 하한", kb >= preset["min_kb"], f"{kb:.1f}KB / 최소 {preset['min_kb']}KB")
    img = im.convert("RGB")
    for r in check_image(img, {"layers": []}, preset, {}):
        if "글자" in r["check"] or "텍스트" in r["check"]:
            continue
        rows.append(r)
    # 좌우 240px 구역의 "밝은 글자 같은 고대비 획" 존재 여부는 눈으로 확인해야 하므로 여기서는 좌우 구역 통계만 제공
    z = preset.get("safe_zone")
    if z:
        a = np.asarray(img).astype(int)
        L, R = a[:, : z["left"]], a[:, W - z["right"]:]
        rows.append({"check": "참고: 좌/우 240px 구역 평균 밝기", "ok": True, "warn": True,
                     "detail": f"좌 {L.mean():.0f} / 우 {R.mean():.0f} (이 구역에 글자가 있는지는 확대 이미지로 직접 확인)"})
    return rows



def _runs(v):
    idx = [i for i, x in enumerate(v) if x]
    out, start, prev = [], None, None
    for i in idx:
        if start is None:
            start = prev = i
        elif i == prev + 1:
            prev = i
        else:
            out.append((start, prev))
            start = prev = i
    if start is not None:
        out.append((start, prev))
    return out


def check_text_integrity(layout, preset, layers):
    """글자가 잘렸는지(레이어 경계에 걸친 획), 한쪽으로 쏠렸는지, 줄 정렬이 어긋났는지 검사."""
    import numpy as np
    rows = []
    W, H = preset["size"]
    texts = [L for L in layout["layers"] if L["type"] == "text" and L["id"] in layers]

    # (1) 잘림: 레이어 경계 행/열에 알파가 8px 이상 연속으로 걸치면 글자·낙서가 bbox에서 잘린 것
    for L in texts:
        a = np.asarray(layers[L["id"]])[..., 3]
        bad = []
        for side, v in (("위", a[0] > 100), ("아래", a[-1] > 100), ("왼쪽", a[:, 0] > 100), ("오른쪽", a[:, -1] > 100)):
            long_runs = [r for r in _runs(v) if r[1] - r[0] + 1 >= 8]
            if long_runs:
                bad.append(f"{side}(연속 {max(r[1]-r[0]+1 for r in long_runs)}px)")
        rows.append({"check": f"글자 잘림 [{L['id']}]", "ok": not bad,
                     "detail": ("경계에서 잘림 의심 - " + ", ".join(bad)) if bad else "경계에 걸친 획 없음"})

    # (2) 정렬: 줄들의 중심(또는 왼쪽 끝)이 가지런한지. 같은 높이에 나란한 요소(알약 등)는 한 줄로 묶는다.
    if len(texts) >= 3:
        boxes = {L["id"]: _box(L) for L in texts}
        rows_ = []
        for k, b in sorted(boxes.items(), key=lambda kv: kv[1][1]):
            for r in rows_:
                ov = min(b[3], r["y1"]) - max(b[1], r["y0"])
                if ov > 0.5 * min(b[3] - b[1], r["y1"] - r["y0"]):
                    r["ids"].append(k); r["y0"] = min(r["y0"], b[1]); r["y1"] = max(r["y1"], b[3])
                    break
            else:
                rows_.append({"ids": [k], "y0": b[1], "y1": b[3]})
        if len(rows_) >= 3:
            spans = {tuple(r["ids"]): (min(boxes[i][0] for i in r["ids"]), max(boxes[i][2] for i in r["ids"])) for r in rows_}
            cs = {k: (a + b) / 2 for k, (a, b) in spans.items()}
            ls = {k: a for k, (a, _) in spans.items()}
            gw = max(b for _, b in spans.values()) - min(a for a, _ in spans.values())

            def sd(d):
                v = list(d.values()); m = sum(v) / len(v)
                return (sum((x - m) ** 2 for x in v) / len(v)) ** 0.5
            centered = sd(cs) < sd(ls)
            ref = sorted((cs if centered else ls).values())[len(rows_) // 2]
            off = {k: (cs if centered else ls)[k] - ref for k in spans}
            worst = max(off.items(), key=lambda kv: abs(kv[1]))
            ok = abs(worst[1]) <= 0.08 * gw
            rows.append({"check": "글자 줄 정렬", "ok": True, "warn": bool(not ok),
                         "detail": (f"{'중앙' if centered else '좌측'} 정렬 기준, 가장 어긋난 줄 [{'+'.join(worst[0])}] {worst[1]:+.0f}px "
                                    f"(블록 폭 {gw}px의 {abs(worst[1])/gw:.0%})")})

    # (3) 쏠림: 글자 질량 중심이 안전영역 중앙에서 얼마나 벗어났는지, 반대편 피사체가 균형을 잡는지
    if texts:
        zl, zt, zr, zb = _zone(preset)
        def centroid(Ls):
            m = sx = sy = 0.0
            for L in Ls:
                a = np.asarray(layers[L["id"]])[..., 3] > 128
                n = a.sum()
                if not n:
                    continue
                ys, xs = np.nonzero(a)
                m += n * L["scale"] ** 2
                sx += (L["x"] + xs.mean() * L["scale"]) * n * L["scale"] ** 2
                sy += (L["y"] + ys.mean() * L["scale"]) * n * L["scale"] ** 2
            return (sx / m, sy / m) if m else (W / 2, H / 2)
        tx, ty = centroid(texts)
        subj = [L for L in layout["layers"] if L["type"] != "text" and L["id"] in layers and L["importance"] == "critical"]
        zc = (zl + zr) / 2
        dev = (tx - zc) / (zr - zl)
        balanced = False
        if subj:
            sx_, _ = centroid(subj) if all(L["id"] in layers for L in subj) else (W / 2, 0)
            balanced = (sx_ - zc) * (tx - zc) < 0
        bad = abs(dev) > 0.2 and not balanced
        rows.append({"check": "글자 블록 쏠림", "ok": True, "warn": bool(bad),
                     "detail": f"글자 중심 x={tx:.0f} (안전영역 중앙 {zc:.0f}, 편차 {dev:+.0%})" + (" - 반대편 피사체가 균형을 잡음" if balanced and abs(dev) > 0.2 else "")})
    return rows


def check_clearance(layout, preset, layers):
    """bbox가 겹치도록 허용한 인물(overlap_ok)과 글자의 간격을 실제 인물 실루엣 픽셀 기준으로 검사한다."""
    import numpy as np
    import cv2
    from PIL import Image
    W, H = preset["size"]
    rows = []
    gap = preset["min_gap"]
    texts = [L for L in layout["layers"] if L["type"] == "text"]
    for S_ in [L for L in layout["layers"] if L.get("overlap_ok") and L["id"] in layers]:
        im = layers[S_["id"]]
        w, h = max(1, round(im.width * S_["scale"])), max(1, round(im.height * S_["scale"]))
        a = np.asarray(im.resize((w, h), Image.NEAREST))[..., 3] > 128
        canvas = np.zeros((H, W), np.uint8)
        x0, y0 = S_["x"], S_["y"]
        cx0, cy0, cx1, cy1 = max(0, x0), max(0, y0), min(W, x0 + w), min(H, y0 + h)
        if cx1 > cx0 and cy1 > cy0:
            canvas[cy0:cy1, cx0:cx1] = a[cy0 - y0:cy1 - y0, cx0 - x0:cx1 - x0]
        grown = cv2.dilate(canvas, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * gap + 1, 2 * gap + 1)))
        for T in texts:
            if S_.get("on_band") and T.get("group") == "footer":
                continue   # 띠 위에 선 인물과 띠 안 문구는 서로 다른 층
            b = _box(T)
            hit = int(grown[max(0, b[1]):min(H, b[3]), max(0, b[0]):min(W, b[2])].sum())
            rows.append({"check": f"인물 실루엣↔글자 간격 [{S_['id']}↔{T['id']}]", "ok": hit == 0,
                         "detail": "실루엣에서 %dpx 이상 떨어짐" % gap if hit == 0 else f"실루엣 {gap}px 안에 글자 영역이 들어옴({hit}px)"})
    return rows
