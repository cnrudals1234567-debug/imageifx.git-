"""Claude Code 헤드리스(`claude -p`) 실행과 stream-json 파싱. 토큰 사용량은 마지막 result 이벤트에서 읽는다."""
import json, os, re, shutil, subprocess

from . import store

ALLOWED_BASH = ["Bash(python -m uv run python -m imageifx:*)", "Bash(python -m uv run python -m imageifx *)"]


class AgentError(RuntimeError):
    pass


def parse_usage(result_event):
    u = result_event.get("usage") or {}
    return {"input": u.get("input_tokens", 0), "output": u.get("output_tokens", 0),
            "cache_read": u.get("cache_read_input_tokens", 0), "cache_creation": u.get("cache_creation_input_tokens", 0)}


def extract_json_block(text):
    """에이전트 최종 답변의 마지막 ```json ... ``` 블록(없으면 마지막 { } 덩어리)을 dict로."""
    blocks = re.findall(r"```json\s*(.*?)```", text or "", re.S)
    cands = blocks[::-1] or ([text[text.rfind("{"):]] if text and "{" in text else [])
    for c in cands:
        try:
            return json.loads(c)
        except ValueError:
            continue
    return None


def _brief(event):
    """진행 로그 한 줄(도구 호출만)."""
    out = []
    for c in (event.get("message") or {}).get("content") or []:
        if isinstance(c, dict) and c.get("type") == "tool_use":
            inp = c.get("input") or {}
            arg = inp.get("file_path") or inp.get("command") or ""
            out.append(f"{c.get('name')} {str(arg)[:110]}")
    return out


def run_claude(prompt, agent, allowed_tools, on_progress=None, timeout=1800, claude_bin=None):
    """성공 시 {text, usage, cost, ms, turns, is_error}. claude_bin 은 테스트에서 가짜 실행 파일로 바꾼다."""
    exe = claude_bin or os.environ.get("IMAGEIFX_CLAUDE_BIN") or shutil.which("claude")
    if not exe:
        raise AgentError("claude 실행 파일을 찾지 못했습니다(Claude Code 설치 확인).")
    cmd = [exe, "-p", "--agent", agent, "--output-format", "stream-json", "--verbose",
           "--allowedTools", ",".join(allowed_tools)]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.Popen(cmd, cwd=store.ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         env=env, text=True, encoding="utf-8")
    try:
        p.stdin.write(prompt)
        p.stdin.close()
    except OSError:
        pass
    result, last_text = None, ""
    import threading
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
            if ev.get("type") == "assistant" and on_progress:
                for b in _brief(ev):
                    on_progress(b)
            elif ev.get("type") == "result":
                result = ev
        p.wait()
    finally:
        timer.cancel()
    if result is None:
        err = (p.stderr.read() or "")[-500:]
        raise AgentError(f"claude가 결과 없이 끝났습니다(코드 {p.returncode}). {err}")
    return {"text": result.get("result") or "", "usage": parse_usage(result), "cost": result.get("total_cost_usd") or 0,
            "ms": result.get("duration_ms") or 0, "turns": result.get("num_turns") or 0,
            "is_error": bool(result.get("is_error"))}
