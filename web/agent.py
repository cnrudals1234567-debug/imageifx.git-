"""Claude Code 헤드리스(`claude -p`) 실행과 stream-json 파싱. 토큰 사용량은 마지막 result 이벤트에서 읽는다.

토큰을 아끼기 위한 가벼운 호출(lean): 저장소 CLAUDE.md·메모리·MCP·스킬이 첫 입력에 실리지 않도록
작업 폴더를 저장소 밖(output/_jobs/<id>/agent)으로 두고, 짧은 시스템 프롬프트와 최소 도구만 준다.
`--bare`는 구독 로그인(OAuth)을 읽지 않으므로 쓰지 않는다."""
import json, os, re, shutil, subprocess, threading

from . import store

ALLOWED_BASH = ["Bash(python -m uv run python -m imageifx:*)", "Bash(python -m uv run python -m imageifx *)"]
LEAN_FLAGS = ["--strict-mcp-config", "--disable-slash-commands", "--no-session-persistence",
              "--exclude-dynamic-system-prompt-sections"]
DEFAULT_MODEL = os.environ.get("IMAGEIFX_MODEL", "sonnet")
DEFAULT_EFFORT = os.environ.get("IMAGEIFX_EFFORT", "low")


class AgentError(RuntimeError):
    pass


def parse_usage(u):
    u = u or {}
    return {"input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0),
            "cache_read": u.get("cache_read_input_tokens", 0), "cache_creation": u.get("cache_creation_input_tokens", 0)}


def billable(u):
    """과금 대상 토큰(입력+출력+캐시 생성). 캐시 읽기는 제외."""
    return u["input"] + u["output"] + u["cache_creation"]


def extract_json_block(text):
    """답변의 마지막 ```json ... ``` 블록(없으면 마지막 { } 덩어리)을 dict로."""
    blocks = re.findall(r"```json\s*(.*?)```", text or "", re.S)
    cands = blocks[::-1] or ([text[text.find("{"):]] if text and "{" in text else [])
    for c in cands:
        try:
            return json.loads(c)
        except ValueError:
            continue
    return None


def _brief(event):
    out = []
    for c in (event.get("message") or {}).get("content") or []:
        if isinstance(c, dict) and c.get("type") == "tool_use":
            inp = c.get("input") or {}
            out.append(f"{c.get('name')} {str(inp.get('file_path') or inp.get('command') or '')[:110]}")
    return out


def build_cmd(exe, *, agent=None, system_prompt=None, tools=None, allowed_tools=None, model=None, effort=None,
              lean=False, max_budget_usd=None):
    cmd = [exe, "-p", "--output-format", "stream-json", "--verbose"]
    if agent:
        cmd += ["--agent", agent]
    if lean:
        cmd += LEAN_FLAGS
    if system_prompt:
        cmd += ["--system-prompt", system_prompt]
    if tools is not None:   # "" 이면 도구 없음
        cmd += ["--tools", ",".join(tools) if not isinstance(tools, str) else tools]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    if max_budget_usd:
        cmd += ["--max-budget-usd", str(max_budget_usd)]
    return cmd


def run_claude(prompt, *, cwd=None, on_progress=None, timeout=1800, claude_bin=None, max_tokens=None, **opts):
    """{text, usage, cost, ms, turns, is_error, aborted}. 호출 중 과금 토큰이 max_tokens를 넘으면 프로세스를 끊고
    그때까지의 사용량을 aborted=True 로 돌려준다(호출자가 사용량을 기록할 수 있도록 예외를 던지지 않는다)."""
    exe = claude_bin or os.environ.get("IMAGEIFX_CLAUDE_BIN") or shutil.which("claude")
    if not exe:
        raise AgentError("claude 실행 파일을 찾지 못했습니다(Claude Code 설치 확인).")
    cmd = build_cmd(exe, **opts)
    p = subprocess.Popen(cmd, cwd=cwd or store.ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         env=dict(os.environ, PYTHONIOENCODING="utf-8"), text=True, encoding="utf-8")
    try:
        p.stdin.write(prompt)
        p.stdin.close()
    except OSError:
        pass
    result, aborted = None, False
    seen = {}   # 메시지 id -> usage (같은 메시지가 content 블록마다 반복되므로 id로 합친다)
    timer = threading.Timer(timeout, p.kill)
    timer.start()
    try:
        for line in p.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "assistant":
                m = ev.get("message") or {}
                if m.get("id") and m.get("usage"):
                    seen[m["id"]] = parse_usage(m["usage"])
                if on_progress:
                    for b in _brief(ev):
                        on_progress(b)
                if max_tokens and sum(billable(u) for u in seen.values()) > max_tokens:
                    aborted = True
                    p.kill()
                    break
            elif ev.get("type") == "result":
                result = ev
        p.wait()
    finally:
        timer.cancel()
    if aborted:
        tot = {k: sum(u[k] for u in seen.values()) for k in ("input", "output", "cache_read", "cache_creation")}
        return {"text": "", "usage": tot, "cost": 0, "ms": 0, "turns": len(seen), "is_error": True, "aborted": True}
    if result is None:
        raise AgentError(f"claude가 결과 없이 끝났습니다(코드 {p.returncode}). {(p.stderr.read() or '')[-500:]}")
    return {"text": result.get("result") or "", "usage": parse_usage(result.get("usage")),
            "cost": result.get("total_cost_usd") or 0, "ms": result.get("duration_ms") or 0,
            "turns": result.get("num_turns") or 0, "is_error": bool(result.get("is_error")), "aborted": False}
