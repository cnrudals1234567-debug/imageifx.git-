"""사용:
  python -m imageifx run input/a.png samples/a.json --preset gfa_1250x560
  python -m imageifx relayout output/<작업폴더> --tag X [--preset P] [--style S]   (분해 캐시 재사용)
  python -m imageifx render output/<작업폴더>   (layout.json 수정 후 재렌더, 1초 내)
  python -m imageifx verify <최종파일> --preset gfa_1250x560   (저장된 파일만으로 독립 검증)
analysis json: {"style": "dark_board|photo_collage", "elements": [{"id","type","importance","bbox":[x0,y0,x1,y1]}]}
배치 옵션은 스타일 프로필(presets/styles)이 정한다. --person-h 등 CLI 옵션은 덮어쓰기 전용이며 layout.json과 보고서에 기록된다.
"""
import argparse, json, os, shutil, sys
from PIL import Image, ImageDraw
from . import decompose, layout as lay, compose, validate, spec

ROOT = spec.ROOT
S = spec.get
DEFAULT_PRESET = "gfa_1250x560"


def _presets():
    with open(os.path.join(ROOT, "presets", "presets.json"), encoding="utf-8") as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}


def _export(out, workdir, name, preset):
    """PNG로 저장하고, 용량 한도를 넘으면 JPG 품질을 낮춰 한도 안으로 맞춘다. (최종 파일 경로, KB) 반환."""
    outp = os.path.join(workdir, f"{name}.png")
    out.save(outp)
    kb = os.path.getsize(outp) / 1000  # 1KB=1000바이트 기준(1024 기준이면 한도를 넘을 수 있어 보수적으로 판정)
    max_kb = preset.get("max_kb")
    margin = S("export.size_margin")
    if max_kb and kb > max_kb * margin:   # PNG가 용량 한도를 넘으면 JPG 품질을 낮춰 한도 안으로
        # 글자 번짐이 적은 4:4:4부터 시도하고, 그래도 한도를 넘으면 4:2:0으로 내림. 한도 이하에서 가장 높은 품질을 선택
        jp = os.path.join(workdir, f"{name}.jpg")
        done = False
        for sub in S("export.subsamplings"):
            for q in range(S("export.jpg_quality_start"), S("export.jpg_quality_min") - 1, -1):
                out.save(jp, quality=q, subsampling=sub, optimize=True)
                if os.path.getsize(jp) / 1000 < max_kb * margin:   # 상한 바로 밑이 아니라 여유를 둠
                    outp, kb = jp, os.path.getsize(jp) / 1000
                    print(f"PNG 용량 초과 -> JPG q={q}, subsampling={'4:4:4' if sub == 0 else '4:2:0'} ({kb:.0f}KB)")
                    done = True
                    break
            if done:
                break
    return outp, kb


def _finish(workdir, name, layout, preset, layers, clean):
    bg = compose.build_bg(clean, layout, workdir)
    out = compose.render(bg, layers, layout)
    outp, kb = _export(out, workdir, name, preset)
    report = validate.check(layout, preset, out.size, kb)
    report += validate.check_image(out, layout, preset, layers)
    report += validate.check_text_integrity(layout, preset, layers)
    report += validate.check_clearance(layout, preset, layers)
    if layout.get("overrides"):
        report.append({"check": "디자인 옵션 덮어쓰기", "ok": True, "warn": True,
                       "detail": f"스타일 '{layout.get('style')}' 기본값에서 벗어남: {layout['overrides']} (일관성을 위해 가능하면 스타일 프로필을 고칠 것)"})
    ep = os.path.join(workdir, "elements.json")
    if os.path.exists(ep):
        with open(ep, encoding="utf-8") as f:
            for r in validate.inventory(json.load(f)["elements"], layout, preset):
                report.append({"check": f"요소 보존 [{r['id']}] {r['kind']}", "ok": r["ok"], "warn": r.get("warn", False),
                               "detail": f"{r['status']} - {r['detail']}"})

    dbg = out.copy()
    d = ImageDraw.Draw(dbg)
    W, H = preset["size"]
    mx, my = preset["safe_margin"]["x"], preset["safe_margin"]["y"]
    d.rectangle([mx, my, W - mx, H - my], outline=(255, 0, 0), width=2)
    for L in layout["layers"]:
        b = validate._box(L)
        d.rectangle(b, outline=(0, 200, 255), width=1)
        d.text((b[0] + 2, b[1] + 2), L["id"], fill=(0, 200, 255))
    dbg.save(os.path.join(workdir, "debug.png"))

    if os.path.exists(ep):
        with open(ep, encoding="utf-8") as f:
            srcp = json.load(f).get("source")
        if srcp and os.path.exists(srcp):
            o = Image.open(srcp).convert("RGB")
            o = o.resize((round(o.width * out.height / o.height), out.height), Image.LANCZOS)
            cmp_ = Image.new("RGB", (o.width + 12 + out.width, out.height), (255, 0, 255))
            cmp_.paste(o, (0, 0))
            cmp_.paste(out, (o.width + 12, 0))
            cmp_.save(os.path.join(workdir, "compare.png"))   # 왼쪽 원본 | 오른쪽 결과
    with open(os.path.join(workdir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    bad = [r for r in report if not r["ok"]]
    for r in report:
        print(("PASS" if not r.get("warn") else "WARN") if r["ok"] else "FAIL", r["check"], "-", r["detail"])
    print(f"\n출력: {outp}  ({'모두 통과' if not bad else str(len(bad)) + '건 실패'})")
    return not bad


def _opts(sp):
    sp.add_argument("--style", default=None, help="스타일 프로필(presets/styles). 생략하면 분석 json의 style 사용")
    # 아래는 스타일 값을 덮어쓰는 옵션. 쓰면 layout.json/보고서에 기록된다.
    sp.add_argument("--text-side", default=None, choices=["left", "right"])
    sp.add_argument("--bg-top", type=int, default=None, help="캔버스 맨 위에 올 원본 y좌표")
    sp.add_argument("--text-scale", type=float, default=None)
    sp.add_argument("--person-h", type=int, default=None, help="피사체 최대 높이(px)")
    sp.add_argument("--scrim", dest="scrim", action="store_true", default=None, help="글자 뒤 흰 스크림 켜기")
    sp.add_argument("--no-scrim", dest="scrim", action="store_false", help="스크림 끄기")


def _overrides(a):
    return {"text_side": a.text_side, "bg_top": a.bg_top, "text_scale": a.text_scale,
            "person_h": a.person_h, "scrim": a.scrim}


def _need_style(name):
    if not name:
        print("스타일이 지정되지 않았습니다. 분석 json에 \"style\"을 쓰거나 --style을 주세요. "
              f"사용 가능: {', '.join(spec.style_names())}")
        sys.exit(2)
    return name, spec.style(name)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("image")
    r.add_argument("analysis")
    r.add_argument("--preset", default=DEFAULT_PRESET)
    _opts(r)
    rl = sub.add_parser("relayout", help="분해 캐시를 재사용해 새 배치안을 별도 폴더로 생성")
    rl.add_argument("workdir")
    rl.add_argument("--tag", required=True)
    rl.add_argument("--preset", default=None)
    _opts(rl)
    vf = sub.add_parser("verify", help="저장된 최종 파일만으로 규정 검증(독립 검토용)")
    vf.add_argument("file")
    vf.add_argument("--preset", default=DEFAULT_PRESET)
    rr = sub.add_parser("render")
    rr.add_argument("workdir")
    a = ap.parse_args()

    if a.cmd == "verify":
        rows = validate.verify_file(a.file, _presets()[a.preset])
        for r in rows:
            print(("PASS" if not r.get("warn") else "WARN") if r["ok"] else "FAIL", r["check"], "-", r["detail"])
        sys.exit(0 if all(r["ok"] for r in rows) else 1)
    if a.cmd == "run":
        preset = _presets()[a.preset]
        src = Image.open(a.image).convert("RGB")
        with open(a.analysis, encoding="utf-8") as f:
            data = json.load(f)
        els = data["elements"]
        style_name, style_dict = _need_style(a.style or data.get("style"))
        base = os.path.splitext(os.path.basename(a.image))[0]
        name = f"{base}_{a.preset}"
        workdir = os.path.join(ROOT, "output", name)
        os.makedirs(workdir, exist_ok=True)
        clean, layers = decompose.run(src, els, text_mask=style_dict.get("text_mask", "bbox"))
        clean.save(os.path.join(workdir, "clean_bg.png"))
        for k, im in layers.items():
            im.save(os.path.join(workdir, f"layer_{k}.png"))
        layout = lay.build(els, src.size, preset, style_dict, _overrides(a), style_name)
        with open(os.path.join(workdir, "elements.json"), "w", encoding="utf-8") as f:
            json.dump({"elements": els, "src_size": list(src.size), "source": os.path.abspath(a.image),
                       "style": style_name}, f, ensure_ascii=False)
        layout.update({"preset": a.preset, "canvas": preset["size"], "name": name})
        with open(os.path.join(workdir, "layout.json"), "w", encoding="utf-8") as f:
            json.dump(layout, f, indent=1)
        sys.exit(0 if _finish(workdir, name, layout, preset, layers, clean) else 1)
    elif a.cmd == "relayout":
        base = a.workdir if os.path.isabs(a.workdir) else os.path.join(ROOT, a.workdir)
        with open(os.path.join(base, "layout.json"), encoding="utf-8") as f:
            old = json.load(f)
        with open(os.path.join(base, "elements.json"), encoding="utf-8") as f:
            meta = json.load(f)
        style_name, style_dict = _need_style(a.style or meta.get("style") or old.get("style"))
        pname = a.preset or old["preset"]
        preset = _presets()[pname]
        name = f"{old['name']}_{a.tag}"
        workdir = os.path.join(ROOT, "output", name)
        os.makedirs(workdir, exist_ok=True)
        for fn in os.listdir(base):
            if fn == "clean_bg.png" or fn.startswith("layer_"):
                shutil.copy(os.path.join(base, fn), workdir)
        meta["style"] = style_name
        with open(os.path.join(workdir, "elements.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False)
        layout = lay.build(meta["elements"], tuple(meta["src_size"]), preset, style_dict, _overrides(a), style_name)
        layout.update({"preset": pname, "canvas": preset["size"], "name": name})
        with open(os.path.join(workdir, "layout.json"), "w", encoding="utf-8") as f:
            json.dump(layout, f, indent=1)
        clean = Image.open(os.path.join(workdir, "clean_bg.png")).convert("RGB")
        layers = {L["id"]: Image.open(os.path.join(workdir, f"layer_{L['id']}.png")) for L in layout["layers"]}
        sys.exit(0 if _finish(workdir, name, layout, preset, layers, clean) else 1)
    else:
        workdir = a.workdir if os.path.isabs(a.workdir) else os.path.join(ROOT, a.workdir)
        with open(os.path.join(workdir, "layout.json"), encoding="utf-8") as f:
            layout = json.load(f)
        preset = _presets()[layout["preset"]]
        clean = Image.open(os.path.join(workdir, "clean_bg.png")).convert("RGB")
        layers = {L["id"]: Image.open(os.path.join(workdir, f"layer_{L['id']}.png")) for L in layout["layers"]}
        sys.exit(0 if _finish(workdir, layout["name"], layout, preset, layers, clean) else 1)


if __name__ == "__main__":
    main()
