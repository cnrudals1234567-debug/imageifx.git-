"""imageifx 웹 UI.  실행: python -m uv run uvicorn web.app:app --reload   (사내 공유: --host 0.0.0.0 --port 8000)"""
import io, json, os, secrets
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image

from imageifx import cli, spec
from . import ops, store, worker

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_UPLOAD = 20 * 1024 * 1024
GROUPS = [("gfa", "네이버 GFA"), ("meta", "메타(페이스북·인스타그램)"), ("google", "구글")]
_basic = HTTPBasic(auto_error=False)


def auth(cred: HTTPBasicCredentials = Depends(_basic)):
    """IMAGEIFX_WEB_PASSWORD 가 설정된 경우에만 공용 비밀번호를 요구한다(사내 공유용)."""
    pw = os.environ.get("IMAGEIFX_WEB_PASSWORD")
    if pw and not (cred and secrets.compare_digest(cred.password, pw)):
        raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})


@asynccontextmanager
async def lifespan(app):
    worker.start()
    yield


app = FastAPI(lifespan=lifespan, dependencies=[Depends(auth)])
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
tpl = Jinja2Templates(directory=os.path.join(HERE, "templates"))
tpl.env.filters["n"] = lambda v: f"{int(v or 0):,}"


def catalog():
    """웹에 보이는 사이즈 목록을 매체 묶음별로. hidden 프리셋(근거 없는 임시값 등)은 뺀다."""
    allp = cli._presets()
    return [(g, label, [(k, v) for k, v in allp.items() if v.get("group") == g and not v.get("hidden")]) for g, label in GROUPS]


def _ctx(**kw):
    return dict(usage_today=store.usage_today(), usage_month=store.usage_month(), daily_limit=worker.DAILY_TOKEN_LIMIT,
                presets=cli._presets(), groups=catalog(), quick_labels=ops.QUICK_LABELS, **kw)


def _job(jid):
    j = store.load(jid) if jid.startswith("j") and "/" not in jid and "\\" not in jid else None
    if not j:
        raise HTTPException(404, "작업이 없습니다")
    return j


def _reports(j, only=None):
    out = {}
    for p, r in j["results"].items():
        if only and p != only:
            continue
        rp = store.ab(r["report"]) if r.get("report") else None
        if rp and os.path.exists(rp):
            with open(rp, encoding="utf-8") as f:
                out[p] = json.load(f)
    return out


def _avg_tokens(kind):
    es = [store.total_tokens(e) for e in store.all_usage() if e["kind"] == kind]
    return round(sum(es) / len(es)) if es else 0


def _limit_hit():
    return bool(worker.DAILY_TOKEN_LIMIT) and store.usage_today()["total"] >= worker.DAILY_TOKEN_LIMIT


def _fix_gate(j, preset):
    """(수정 가능, 막힌 이유). 사이즈마다 독립: 다른 사이즈의 요청이 대기 중이어도 큐에 쌓을 수 있다."""
    r = j["results"].get(preset)
    if not r or "error" in r:
        return False, "이 사이즈는 변환 결과가 없습니다."
    if j["status"] not in ("done", "fixing"):
        return False, "작업이 진행 중입니다."
    if store.pending_fix(j, preset):
        return False, "이 사이즈의 수정이 진행 중입니다."
    return True, ""


def _ai_gate(j, preset):
    n_ai = sum(1 for fx in j["fixes"] if fx["preset"] == preset and fx.get("kind") == "ai" and fx["status"] == "done")
    if n_ai >= worker.MAX_AI_FIX_PER_SIZE:
        return False, f"AI 수정은 사이즈당 {worker.MAX_AI_FIX_PER_SIZE}회까지입니다. 빠른 조정·자동 보정은 계속 쓸 수 있습니다."
    if _limit_hit():
        return False, "오늘 토큰 상한에 도달해 AI 수정을 막았습니다. 빠른 조정·자동 보정은 쓸 수 있습니다."
    return True, ""


def _job_view(j):
    active = j["status"] in store.ACTIVE
    return dict(job=j, active=active, total=store.sum_usage(j["usage"]), reports=_reports(j),
                style_docs=[(n, str(spec.style(n).get("_doc", ""))) for n in spec.style_names()],
                missing=[(g, label, [(k, v) for k, v in items if k not in j["results"]]) for g, label, items in catalog()])


def _size_view(j, preset):
    if preset not in j["presets"] and preset not in j["results"]:
        raise HTTPException(404, "이 작업에 없는 사이즈입니다")
    ok, why = _fix_gate(j, preset)
    ai_ok, ai_why = _ai_gate(j, preset)
    usage = [u for u in j["usage"]]
    r = j["results"].get(preset, {})
    losts = []
    rep = _reports(j, preset).get(preset, [])
    for row in rep:   # 빠진 장식(LOST 등)은 버튼 한 번으로 빼서 PASS로 만들 수 있다(토큰 0)
        if not row["ok"] and row["check"].startswith("요소 보존 [") and "movable" in row["check"]:
            losts.append(row["check"].split("[")[1].split("]")[0])
    return dict(job=j, active=j["status"] in store.ACTIVE, preset=preset, pinfo=cli._presets().get(preset, {}), res=r,
                rows=rep, can_fix=ok, why=why, ai_ok=ai_ok, ai_why=ai_why, losts=losts, total=store.sum_usage(usage),
                fixes=[fx for fx in j["fixes"] if fx["preset"] == preset], est_ai=_avg_tokens("fix"))


@app.get("/")
def index(request: Request):
    return tpl.TemplateResponse(request, "index.html", _ctx(default=cli.DEFAULT_PRESET, recent=store.list_jobs()[:5]))


@app.post("/jobs")
async def create_job(image: UploadFile = File(...), presets: list[str] = Form(...), confirm: str = Form("")):
    allowed = cli._presets()
    presets = [p for p in presets if p in allowed]
    if not presets:
        raise HTTPException(400, "변환할 매체 사이즈를 하나 이상 선택하세요")
    data = await image.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(400, "파일이 20MB를 넘습니다")
    try:
        im = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        raise HTTPException(400, "이미지 파일을 읽을 수 없습니다(png/jpg/webp)")
    if min(im.size) < 200:
        raise HTTPException(400, "이미지가 너무 작습니다(최소 200px)")
    jid = store.new_id()
    os.makedirs(store.INPUT, exist_ok=True)
    src = os.path.join(store.INPUT, jid + ".png")
    im.save(src)
    store.create(jid, source=src, filename=image.filename or "", presets=presets, confirm=bool(confirm), size=list(im.size))
    worker.enqueue({"kind": "analyze", "job": jid})
    return RedirectResponse(f"/jobs/{jid}", status_code=303)


@app.get("/jobs")
def jobs(request: Request):
    return tpl.TemplateResponse(request, "jobs.html", _ctx(jobs=store.list_jobs(), sum_usage=store.sum_usage))


@app.get("/jobs/{jid}")
def job_page(request: Request, jid: str):
    return tpl.TemplateResponse(request, "job.html", _ctx(**_job_view(_job(jid))))


@app.get("/jobs/{jid}/fragment")
def job_fragment(request: Request, jid: str):
    return tpl.TemplateResponse(request, "job_body.html", _ctx(**_job_view(_job(jid))))


@app.get("/jobs/{jid}/size/{preset}")
def size_page(request: Request, jid: str, preset: str):
    return tpl.TemplateResponse(request, "size.html", _ctx(**_size_view(_job(jid), preset)))


@app.get("/jobs/{jid}/size/{preset}/fragment")
def size_fragment(request: Request, jid: str, preset: str):
    return tpl.TemplateResponse(request, "size_body.html", _ctx(**_size_view(_job(jid), preset)))


@app.post("/jobs/{jid}/start")
def start(jid: str, style: str = Form("")):
    """분석 확인 후 실행. 스타일을 잘못 골랐다면 여기서 바꿀 수 있다(토큰 0)."""
    j = _job(jid)
    if j["status"] != "awaiting_confirm":
        raise HTTPException(409, "확인 대기 중인 작업이 아닙니다")
    if style and style != j.get("style"):
        if style not in spec.style_names():
            raise HTTPException(400, "알 수 없는 스타일입니다")
        ap = os.path.join(store.job_dir(jid), "analysis.json")
        with open(ap, encoding="utf-8") as f:
            data = json.load(f)
        data["style"] = style
        with open(ap, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        store.update(jid, lambda x: x.update(style=style))
        store.log(jid, f"스타일을 {style}(으)로 바꿨습니다")
    store.update(jid, lambda x: x.update(status="queued", stage="실행 대기"))
    worker.enqueue({"kind": "convert", "job": jid})
    return RedirectResponse(f"/jobs/{jid}", status_code=303)


@app.post("/jobs/{jid}/reanalyze")
def reanalyze(jid: str, note: str = Form("")):
    """분석이 틀렸을 때 다시 분석한다(토큰 사용, 캐시 무시). 변환 전에만 가능."""
    j = _job(jid)
    if j["status"] not in ("awaiting_confirm", "failed") or j["results"]:
        raise HTTPException(409, "변환 전 확인 단계에서만 다시 분석할 수 있습니다")
    if _limit_hit():
        raise HTTPException(409, "오늘 토큰 상한에 도달했습니다.")
    store.update(jid, lambda x: x.update(status="queued", stage="다시 분석 대기", error=""))
    worker.enqueue({"kind": "analyze", "job": jid, "force": True, "note": note.strip()[:500]})
    return RedirectResponse(f"/jobs/{jid}", status_code=303)


@app.post("/jobs/{jid}/add-sizes")
def add_sizes(jid: str, presets: list[str] = Form(...)):
    """완료된 작업에 사이즈를 더한다. 분석·분해(LaMa)를 다시 하지 않으므로 토큰 0."""
    j = _job(jid)
    allowed = cli._presets()
    new = [p for p in presets if p in allowed and p not in j["results"]]
    if j["status"] != "done" or not j.get("base_workdir"):
        raise HTTPException(409, "변환이 끝난 작업에만 사이즈를 추가할 수 있습니다")
    if not new:
        raise HTTPException(400, "추가할 새 사이즈를 선택하세요")

    def f(x):
        x["presets"] = x["presets"] + [p for p in new if p not in x["presets"]]
        x["status"], x["stage"] = "queued", "사이즈 추가 대기"
    store.update(jid, f)
    worker.enqueue({"kind": "convert", "job": jid, "presets": new})
    return RedirectResponse(f"/jobs/{jid}", status_code=303)


def _queue_fix(jid, preset, kind, label, **task):
    """수정 요청을 큐에 넣는다. 빠른 조정을 이어서 누르면 직전 빠른 조정 기록에 합친다."""
    j = _job(jid)
    ok, why = _fix_gate(j, preset)
    if not ok:
        raise HTTPException(409, why)
    prev = [fx for fx in j["fixes"] if fx["preset"] == preset and fx["status"] == "done" and not fx.get("reverted")]
    merge = (kind == "quick" and prev and prev[-1].get("kind") == "quick" and prev[-1]["after"]["workdir"] == j["results"][preset]["workdir"]
             and not any(o.get("op") == "drop" for o in task.get("ops", [])))
    holder = {}

    def f(x):
        if merge:
            fx = next(y for y in x["fixes"] if y["n"] == prev[-1]["n"])
            fx.update(status="queued", merge=True, label=fx["label"] + " · " + label)
        else:
            fx = {"n": max([y["n"] for y in x["fixes"]] + [0]) + 1, "preset": preset, "kind": kind, "label": label,
                  "status": "queued", "merge": False, "time": store.time.strftime("%Y-%m-%d %H:%M:%S")}
            x["fixes"].append(fx)
        holder["n"] = fx["n"]
        x["status"], x["stage"] = "fixing", "수정 대기"
    store.update(jid, f)
    worker.enqueue({"kind": "fix", "job": jid, "preset": preset, "n": holder["n"], "fix_kind": kind, **task})


@app.post("/jobs/{jid}/size/{preset}/quick")
def quick(jid: str, preset: str, action: str = Form(...), element: str = Form("")):
    """토큰 0 빠른 조정: 글자·인물 이동/배율, 스크림, 빠진 장식 빼기."""
    if action == "drop":
        _queue_fix(jid, preset, "quick", f"장식 빼기 ({element})", ops=[{"op": "drop", "id": element}])
    elif action in ops.QUICK:
        label = dict(ops.QUICK_LABELS)[action]
        _queue_fix(jid, preset, "quick", label, ops=[ops.QUICK[action]])
    else:
        raise HTTPException(400, "알 수 없는 조정입니다")
    return RedirectResponse(f"/jobs/{jid}/size/{preset}", status_code=303)


@app.post("/jobs/{jid}/size/{preset}/auto")
def auto(jid: str, preset: str):
    """무료 자동 보정: 글자 블록 이동·축소로 배치 FAIL을 줄인다(토큰 0)."""
    _queue_fix(jid, preset, "auto", "자동 보정(무료)")
    return RedirectResponse(f"/jobs/{jid}/size/{preset}", status_code=303)


@app.post("/jobs/{jid}/size/{preset}/fix")
def fix(jid: str, preset: str, request: str = Form("")):
    """AI 수정 요청(토큰 사용): 가벼운 호출 1회로 배치 연산만 받아 적용한다."""
    ok, why = _ai_gate(_job(jid), preset)
    if not ok:
        raise HTTPException(409, why)
    request = request.strip()[:1000]
    _queue_fix(jid, preset, "ai", request or "(자동: FAIL·눈에 보이는 결함)", request=request)
    return RedirectResponse(f"/jobs/{jid}/size/{preset}", status_code=303)


@app.post("/jobs/{jid}/size/{preset}/revert")
def revert(jid: str, preset: str):
    """가장 최근 수정을 되돌린다. 수정은 새 폴더에서 이뤄지므로 결과 포인터만 되돌리면 된다."""
    j = _job(jid)
    if j["status"] not in ("done", "fixing") or store.pending_fix(j, preset):
        raise HTTPException(409, "진행 중인 수정이 있어 되돌릴 수 없습니다")
    mine = [fx for fx in j["fixes"] if fx["preset"] == preset and fx["status"] == "done" and not fx.get("reverted")]
    if not mine:
        raise HTTPException(409, "되돌릴 수정이 없습니다")
    fx = mine[-1]

    def f(x):
        x["results"][preset] = fx["before"]
        for y in x["fixes"]:
            if y["n"] == fx["n"]:
                y["reverted"] = True
    store.update(jid, f)
    store.log(jid, f"{preset} 수정 {fx['n']}회차를 되돌림")
    return RedirectResponse(f"/jobs/{jid}/size/{preset}", status_code=303)


@app.get("/jobs/{jid}/files/{path:path}")
def files(jid: str, path: str, download: int = 0):
    _job(jid)
    full = os.path.realpath(os.path.join(store.OUT, path))
    out = os.path.realpath(store.OUT)
    parts = os.path.relpath(full, out).replace("\\", "/").split("/")
    mine = parts[0].startswith(jid + "_") or (parts[0] == "_jobs" and len(parts) > 1 and parts[1] == jid)
    if not full.startswith(out + os.sep) or not mine or not os.path.isfile(full) or \
            os.path.splitext(full)[1].lower() not in (".png", ".jpg", ".jpeg", ".json"):
        raise HTTPException(404)
    return FileResponse(full, filename=os.path.basename(full) if download else None)


@app.get("/usage")
def usage_page(request: Request):
    es = store.all_usage()
    by = {}
    for e in es:
        by.setdefault((e["time"][:10], e["kind"]), []).append(e)
    rows = sorted(((d, k, store.sum_usage(v)) for (d, k), v in by.items()), reverse=True)
    per_job = {}
    for e in es:
        per_job.setdefault(e["job"], []).append(e)
    jobs_ = sorted(((jid, store.sum_usage(v)) for jid, v in per_job.items()), reverse=True)[:30]
    return tpl.TemplateResponse(request, "usage.html", _ctx(rows=rows, jobs_=jobs_, grand=store.sum_usage(es),
                                                            fixc=store.fix_counts(store.list_jobs())))
