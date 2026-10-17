"""imageifx 웹 UI.  실행: python -m uv run uvicorn web.app:app --reload   (사내 공유: --host 0.0.0.0 --port 8000)"""
import json, os, secrets, shutil
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image

from imageifx import cli, spec
from . import fixer, store, worker

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_UPLOAD = 20 * 1024 * 1024
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


def _ctx(**kw):
    return dict(usage_today=store.usage_today(), usage_month=store.usage_month(),
                daily_limit=worker.DAILY_TOKEN_LIMIT, **kw)


def _job(jid):
    j = store.load(jid) if jid.startswith("j") and "/" not in jid and "\\" not in jid else None
    if not j:
        raise HTTPException(404, "작업이 없습니다")
    return j


def _job_view(j):
    active = j["status"] in store.ACTIVE
    tot = store.sum_usage(j["usage"])
    limit_hit = bool(worker.DAILY_TOKEN_LIMIT) and store.usage_today()["total"] >= worker.DAILY_TOKEN_LIMIT
    can_fix = not active and j["status"] in ("done",) and len(j["fixes"]) < worker.MAX_FIX_PER_JOB and not limit_hit
    why = ""
    if limit_hit:
        why = "오늘 토큰 상한에 도달해 수정 요청을 막았습니다."
    elif len(j["fixes"]) >= worker.MAX_FIX_PER_JOB:
        why = f"수정 요청은 작업당 {worker.MAX_FIX_PER_JOB}회까지입니다."
    reports = {}
    for p, r in j["results"].items():
        rp = store.ab(r["report"]) if r.get("report") else None
        if rp and os.path.exists(rp):
            with open(rp, encoding="utf-8") as f:
                reports[p] = json.load(f)
    return dict(job=j, active=active, total=tot, can_fix=can_fix, why=why, reports=reports)


@app.get("/")
def index(request: Request):
    presets = {k: v for k, v in cli._presets().items()}
    return tpl.TemplateResponse(request, "index.html", _ctx(presets=presets, default=cli.DEFAULT_PRESET, recent=store.list_jobs()[:5]))


@app.post("/jobs")
async def create_job(image: UploadFile = File(...), presets: list[str] = Form(...), confirm: str = Form("")):
    allowed = cli._presets()
    presets = [p for p in presets if p in allowed]
    if not presets:
        raise HTTPException(400, "변환할 매체 사이즈를 하나 이상 선택하세요")
    data = await image.read(MAX_UPLOAD + 1)
    if len(data) > MAX_UPLOAD:
        raise HTTPException(400, "파일이 20MB를 넘습니다")
    jid = store.new_id()
    os.makedirs(store.INPUT, exist_ok=True)
    src = os.path.join(store.INPUT, jid + ".png")
    try:
        import io
        im = Image.open(io.BytesIO(data))
        im = im.convert("RGB")
    except Exception:
        raise HTTPException(400, "이미지 파일을 읽을 수 없습니다(png/jpg/webp)")
    if min(im.size) < 200:
        raise HTTPException(400, "이미지가 너무 작습니다(최소 200px)")
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


@app.post("/jobs/{jid}/start")
def start(jid: str):
    j = _job(jid)
    if j["status"] != "awaiting_confirm":
        raise HTTPException(409, "확인 대기 중인 작업이 아닙니다")
    store.update(jid, lambda x: x.update(status="queued", stage="실행 대기"))
    worker.enqueue({"kind": "convert", "job": jid})
    return RedirectResponse(f"/jobs/{jid}", status_code=303)


@app.post("/jobs/{jid}/fix")
def fix(jid: str, preset: str = Form(...), request: str = Form("")):
    j = _job(jid)
    view = _job_view(j)
    if not view["can_fix"]:
        raise HTTPException(409, view["why"] or "지금은 수정 요청을 할 수 없습니다")
    if preset not in j["results"] or "error" in j["results"][preset]:
        raise HTTPException(400, "수정할 결과가 없는 매체입니다")
    request = request.strip()[:1000]
    n = len(j["fixes"]) + 1

    def f(x):
        x["fixes"].append({"n": n, "preset": preset, "request": request, "status": "running", "time": store.time.strftime("%Y-%m-%d %H:%M:%S")})
        x["status"], x["stage"] = "fixing", "수정 대기"
    store.update(jid, f)
    worker.enqueue({"kind": "fix", "job": jid, "preset": preset, "n": n, "request": request})
    return RedirectResponse(f"/jobs/{jid}", status_code=303)


@app.post("/jobs/{jid}/revert")
def revert(jid: str, preset: str = Form(...), n: int = Form(...)):
    j = _job(jid)
    if j["status"] in store.ACTIVE:
        raise HTTPException(409, "작업이 실행 중입니다")
    mine = [fx for fx in j["fixes"] if fx["preset"] == preset and fx["status"] == "done" and not fx.get("reverted")]
    if not mine or mine[-1]["n"] != n:
        raise HTTPException(409, "되돌릴 수 있는 것은 이 매체의 가장 최근 수정뿐입니다")
    fx = mine[-1]
    fixer.restore(fx["before"], fx["before_snap"])

    def f(x):
        x["results"][preset] = fx["before"]
        for y in x["fixes"]:
            if y["n"] == n:
                y["reverted"] = True
    store.update(jid, f)
    store.log(jid, f"수정 {n}회차를 되돌림")
    return RedirectResponse(f"/jobs/{jid}", status_code=303)


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
    return tpl.TemplateResponse(request, "usage.html", _ctx(rows=rows, jobs_=jobs_, grand=store.sum_usage(es)))
