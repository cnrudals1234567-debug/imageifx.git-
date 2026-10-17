"""사용:
  python -m imageifx run input/a.png analysis/a.json --preset gfa_1200x560
  python -m imageifx render output/a_gfa_1200x560   (layout.json 수정 후 재렌더, 1초 내)
analysis json: {"elements":[{"id","type":"text|subject|decor","importance":"critical|decorative","bbox":[x0,y0,x1,y1]}]}
"""
import argparse, json, os, shutil, sys
from PIL import Image, ImageDraw
from . import decompose, layout as lay, compose, validate

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _presets():
    with open(os.path.join(ROOT, "presets", "presets.json"), encoding="utf-8") as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith("_")}


def _finish(workdir, name, layout, preset, layers, clean):
    bg = compose.build_bg(clean, layout, workdir)
    out = compose.render(bg, layers, layout)
    outp = os.path.join(workdir, f"{name}.png")
    out.save(outp)
    kb = os.path.getsize(outp) / 1000  # 1KB=1000바이트 기준(1024 기준이면 한도를 넘을 수 있어 보수적으로 판정)
    max_kb = preset.get("max_kb")
    if max_kb and kb > max_kb * 0.96:   # PNG가 용량 한도를 넘으면 JPG 품질을 낮춰 한도 안으로
        # 글자 번짐이 적은 4:4:4부터 시도하고, 그래도 한도를 넘으면 4:2:0으로 내림. 한도 이하에서 가장 높은 품질을 선택
        jp = os.path.join(workdir, f"{name}.jpg")
        done = False
        for sub in (0, 2):
            for q in range(95, 29, -1):
                out.save(jp, quality=q, subsampling=sub, optimize=True)
                if os.path.getsize(jp) / 1000 < max_kb * 0.96:   # 상한 바로 밑이 아니라 4% 여유를 둠
                    outp, kb = jp, os.path.getsize(jp) / 1000
                    print(f"PNG 용량 초과 -> JPG q={q}, subsampling={'4:4:4' if sub == 0 else '4:2:0'} ({kb:.0f}KB)")
                    done = True
                    break
            if done:
                break
    report = validate.check(layout, preset, out.size, kb)
    report += validate.check_image(out, layout, preset, layers)
    report += validate.check_text_integrity(layout, preset, layers)
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
    sp.add_argument("--bg-top", type=int, default=None, help="캔버스 맨 위에 올 원본 y좌표")
    sp.add_argument("--text-scale", type=float, default=None)
    sp.add_argument("--person-h", type=int, default=None, help="피사체 최대 높이(px)")
    sp.add_argument("--scrim", action="store_true", help="글자 뒤 흰 스크림")
    sp.add_argument("--no-scrim", action="store_true", help="프리셋 기본 스크림 끄기 (어두운 배경용)")


def _opt_kwargs(a):
    return dict(bg_top=a.bg_top, text_scale=a.text_scale, person_h=a.person_h,
                scrim="off" if a.no_scrim else a.scrim)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("image")
    r.add_argument("analysis")
    r.add_argument("--preset", default="gfa_1200x560")
    r.add_argument("--text-side", default="left")
    for sp in (r,):
        _opts(sp)
    rl = sub.add_parser("relayout", help="분해 캐시를 재사용해 새 배치안을 별도 폴더로 생성")
    rl.add_argument("workdir"); rl.add_argument("--tag", required=True)
    rl.add_argument("--text-side", default="right"); rl.add_argument("--preset", default=None); _opts(rl)
    vf = sub.add_parser("verify", help="저장된 최종 파일만으로 규정 검증(독립 검토용)")
    vf.add_argument("file"); vf.add_argument("--preset", default="gfa_1250x560")
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
            els = json.load(f)["elements"]
        base = os.path.splitext(os.path.basename(a.image))[0]
        name = f"{base}_{a.preset}"
        workdir = os.path.join(ROOT, "output", name)
        os.makedirs(workdir, exist_ok=True)
        clean, layers = decompose.run(src, els)
        clean.save(os.path.join(workdir, "clean_bg.png"))
        for k, im in layers.items():
            im.save(os.path.join(workdir, f"layer_{k}.png"))
        layout = lay.build(els, src.size, preset, a.text_side, **_opt_kwargs(a))
        json.dump({"elements": els, "src_size": list(src.size), "source": os.path.abspath(a.image)}, open(os.path.join(workdir, "elements.json"), "w", encoding="utf-8"), ensure_ascii=False)
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
        pname = a.preset or old["preset"]
        preset = _presets()[pname]
        name = f"{old['name']}_{a.tag}"
        workdir = os.path.join(ROOT, "output", name)
        os.makedirs(workdir, exist_ok=True)
        for fn in os.listdir(base):
            if fn == "clean_bg.png" or fn.startswith("layer_") or fn == "elements.json":
                shutil.copy(os.path.join(base, fn), workdir)
        layout = lay.build(meta["elements"], tuple(meta["src_size"]), preset, a.text_side, **_opt_kwargs(a))
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
