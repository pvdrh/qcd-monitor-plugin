"""Adapter: file trajectory của 4 scaffold trong CodeTraceBench → chuỗi Step chuẩn.

Quy tắc tách step theo pre-registration Phần A §2 (docs/2026-10-20-p0-probe/preregistration-part-a.md) và amendment 1
(docs/2026-10-20-p1-data-pipeline/preregistration-part-a-amendment-1.md): step thứ k của chuỗi trả về = step_id k (1-based)
trong nhãn. Chỉ đọc file trajectory; không đọc nhãn. Feature của step k chỉ được dùng step 1..k (Phần A §8), nên
observation chỉ gắn vào step mà sau đó nó mới có.
"""
import json
import re
import shlex
import tarfile
from dataclasses import dataclass, field


@dataclass
class Step:
    kind: str  # shell | input | edit | view | python | think | finish | rejected | other
    tool: str  # tên action/tool gốc của scaffold
    command: str = ""  # lệnh shell, code python hoặc phím gửi đi
    args: dict = field(default_factory=dict)  # tham số có cấu trúc của tool (path, file_text, old_str, new_str, ...)
    message: str = ""  # lời của agent đi kèm step
    obs: str = ""  # observation có được sau step
    exit_code: int | None = None
    turn: int = 0  # chỉ số lượt LLM


TASK_PATTERNS = [
    r"<pr_description>\s*(?:Consider the following PR description:\s*)?(.*?)\s*</pr_description>",  # mini-SWE-agent/SWE-agent, SWE-bench
    r"<issue_description>\s*(.*?)\s*</issue_description>",  # OpenHands, SWE-bench
    r"Task Description:\n(.*?)\n\nCurrent terminal state:",  # Terminus2
    r"Please solve this issue: (.*?)\n\nYou can execute bash commands",  # mini-SWE-agent, TerminalBench
    r"ISSUE:\n(.*?)(?:\n\nINSTRUCTIONS:|$)",  # SWE-agent, bản prompt cũ
]


def task_text(raw):
    """Phần mô tả task trong prompt đầu tiên, bỏ template của scaffold."""
    for p in TASK_PATTERNS:
        m = re.search(p, raw, re.S)
        if m:
            return m.group(1).strip()
    return raw.strip()


def text(content):
    return content if isinstance(content, str) else "\n".join(x.get("text", "") for x in content or [] if isinstance(x, dict))


def loads(b):
    try:
        return json.loads(b)
    except ValueError:
        return None


MINI_ACTION = re.compile(r"```bash\s*\n(.*?)\n```", re.S)  # info.config.agent.action_regex của mini-SWE-agent


def mini(files):
    """1 step = 1 message assistant có khối ```bash; observation = message kế tiếp.

    Scaffold chỉ chạy lượt có đúng 1 action khớp action_regex; lượt khác bị từ chối (kind `rejected`), vẫn là 1 step.
    """
    name = next((n for n in files if n.endswith("traj.json")), None)
    if name is None:
        raise LookupError("không có file trajectory (*traj.json)")
    msgs = json.loads(files[name])["messages"]
    first_reply = next((i for i, m in enumerate(msgs) if m.get("role") == "assistant"), len(msgs))
    user = next((m for m in msgs[:first_reply] if m.get("role") == "user"), {})  # observation cũng có role user, nên chỉ xét trước lượt đầu
    steps = []
    for i, m in enumerate(msgs):
        body = text(m.get("content"))
        if m.get("role") != "assistant" or "```bash" not in body:
            continue
        blocks = [b.strip() for b in MINI_ACTION.findall(body)]
        obs = text(msgs[i + 1].get("content")) if i + 1 < len(msgs) else ""
        code = re.search(r"<returncode>\s*(-?\d+)\s*</returncode>", obs)
        steps.append(Step("shell" if len(blocks) == 1 else "rejected", "bash", "\n".join(blocks), message=MINI_ACTION.sub("", body).strip(),
                          obs=obs, exit_code=int(code.group(1)) if code else None, turn=len(steps)))
    return task_text(text(user.get("content"))), steps


SWEAGENT_VIEW = {"open", "goto", "scroll_up", "scroll_down", "search_file", "search_dir", "find_file"}  # lệnh xem của ACI SWE-agent
EDITOR_OPTS = {"--file_text", "--old_str", "--new_str", "--insert_line", "--view_range"}


def editor_args(action):
    """`str_replace_editor <command> <path> [--opt giá trị...]` → dict; giá trị nhiều token nối bằng dấu cách.

    Không tách được bằng shlex (vd. dấu nháy lẻ) thì chỉ giữ command và path.
    """
    try:
        tok = shlex.split(action)
    except ValueError:
        tok = action.split(maxsplit=3)[:3]
    args, opt = {"command": tok[1] if len(tok) > 1 else "", "path": tok[2] if len(tok) > 2 else ""}, None
    for t in tok[3:]:
        if t in EDITOR_OPTS:
            opt = t[2:]
        elif opt:
            args[opt] = f"{args[opt]} {t}" if opt in args else t
    return args


def sweagent(files):
    """1 step = 1 phần tử `trajectory` của file .traj."""
    name = next((n for n in files if n.endswith(".traj")), None)
    if name is None:
        raise LookupError("không có file trajectory (*.traj)")
    t = json.loads(files[name])
    user = next((m for m in t.get("history", []) if m.get("role") == "user" and not m.get("is_demo")), None)
    steps = []
    for e in t["trajectory"]:
        a = (e.get("action") or "").strip()
        first = a.split(maxsplit=1)[0] if a else ""
        s = Step("shell", "bash", a, message=e.get("thought") or "", obs=str(e.get("observation") or ""), turn=len(steps))
        if first == "str_replace_editor":
            s.args = editor_args(a)
            s.kind, s.tool, s.command = "view" if s.args["command"] == "view" else "edit", first, ""
        elif first == "edit":  # ACI cũ: edit a:b <dòng mới> end_of_edit, sửa file đang mở; sai cú pháp thì giữ nguyên lệnh
            m = re.match(r"edit\s+(\d+):(\d+)\n(.*?)\n?end_of_edit", a, re.S)
            state = e.get("state") if isinstance(e.get("state"), dict) else loads(e.get("state") or "{}") or {}  # SWE-agent 1.x: dict
            s.kind, s.tool = "edit", "edit"
            if m:
                s.command, s.args = "", {"command": "edit", "path": state.get("open_file", ""), "start": int(m.group(1)), "end": int(m.group(2)),
                                         "new_str": m.group(3)}
        elif first == "create":  # ACI cũ: tạo file rỗng
            s.kind, s.tool, s.command, s.args = "edit", "create", "", {"command": "create", "path": a.split(maxsplit=1)[1] if " " in a else ""}
        elif first in SWEAGENT_VIEW:
            s.kind, s.tool = "view", first
        elif first == "submit" or first.startswith("exit_"):  # exit_cost, exit_format...: harness tự kết thúc
            s.kind, s.tool = "finish", first
        elif not first:
            s.kind, s.tool = "other", ""
        steps.append(s)
    return task_text(text(user["content"])) if user else "", steps


def terminus2(files):
    """1 step = 1 phần tử `commands` của episode-N/response.txt; response không phải JSON có `commands` → không có step.

    Màn hình sau episode N (episode-(N+1)/prompt.txt) chỉ có sau khi chạy hết lệnh của episode, nên chỉ gắn
    vào lệnh cuối; analysis + plan có trước mọi lệnh, gắn vào lệnh đầu. Keystroke giữ nguyên (\\n là phím Enter).
    """
    eps = {}
    for n, b in files.items():
        m = re.search(r"episode-(\d+)/(response|prompt)\.txt$", n)
        if m:
            eps.setdefault(int(m.group(1)), {})[m.group(2)] = b.decode("utf-8", "replace")
    if not eps:
        raise LookupError("không có file episode-N/response.txt")
    steps = []
    for k in sorted(eps):
        try:
            j = json.loads(eps[k].get("response", ""))
            cmds, thought = [c.get("keystrokes") or "" for c in j.get("commands") or []], f"{j.get('analysis', '')}\n{j.get('plan', '')}".strip()
        except (ValueError, AttributeError, TypeError):
            continue
        for i, c in enumerate(cmds):
            steps.append(Step("shell", "keystrokes", c, message=thought if i == 0 else "",
                              obs=eps.get(k + 1, {}).get("prompt", "") if i == len(cmds) - 1 else "", turn=k))
    return task_text(eps.get(0, {}).get("prompt", "")), steps


OH_KIND = {"run": "shell", "run_ipython": "python", "edit": "edit", "read": "view", "think": "think", "finish": "finish"}
OH_AGENT_LOG = re.compile(r"/agent-logs/[0-9a-f-]+\.json$")  # một số run (GPT-5) lưu cả danh sách event trong 1 file
OH_REFUSED = "ERROR: Cannot execute multiple commands at once"  # runtime từ chối, lệnh không chạy


def openhands_events(files):
    """1 step = 1 action của agent trong sessions/sessions/<id>/events/N.json (hoặc agent-logs/<id>.json), trừ `system`
    và `message`; bỏ event hỏng."""
    ev = {}
    for n, b in files.items():
        m = re.search(r"/events/(\d+)\.json$", n)
        if m and isinstance(e := loads(b), dict):
            ev[int(m.group(1))] = e
    if not ev:
        for n, b in files.items():
            if OH_AGENT_LOG.search(n) and isinstance(lst := loads(b), list):
                ev = {e.get("id", i): e for i, e in enumerate(lst) if isinstance(e, dict)}
    if not ev:
        raise LookupError("không có event OpenHands")
    obs = {}
    for i in sorted(ev):
        if ev[i].get("observation"):
            obs.setdefault(ev[i].get("cause"), ev[i])
    user = next((ev[i] for i in sorted(ev) if ev[i].get("source") == "user" and ev[i].get("action") == "message"), {})
    steps, turns = [], {}
    for i in sorted(ev):
        e, kind = ev[i], ev[i].get("action")
        if e.get("source") != "agent" or kind in (None, "", "system", "message"):
            continue
        a = dict(e.get("args") or {})
        o = obs.get(e.get("id"), {})
        x = o.get("extras") or {}
        code = x.get("exit_code", (x.get("metadata") or {}).get("exit_code"))  # -1: lệnh chưa xong, không có exit code
        resp = ((e.get("tool_call_metadata") or {}).get("model_response") or {}).get("id") or f"event-{i}"
        s = Step("input" if kind == "run" and a.get("is_input") in (True, "true") else OH_KIND.get(kind, "other"), kind,
                 message=a.pop("thought", "") or "", obs=text(o.get("content", "")), exit_code=code if isinstance(code, int) and code != -1 else None,
                 turn=turns.setdefault(resp, len(turns)))
        if kind in ("run", "run_ipython"):
            s.command = a.get("command" if kind == "run" else "code", "")
        else:
            s.args = a
        if kind == "run" and s.obs.startswith(OH_REFUSED):
            s.kind = "rejected"
        steps.append(s)
    return task_text(text((user.get("args") or {}).get("content", ""))), steps


LLM_KIND = {"execute_bash": "shell", "execute_ipython_cell": "python", "think": "think"}
LOG = re.compile(r"-(\d{9,}\.\d+)\.json$")  # log mỗi lần gọi LLM: <model>-<unix_ts>.json


def tool_calls(msgs):
    return [(m, tc) for m in msgs for tc in m.get("tool_calls") or [] if tc["function"]["name"] != "finish"]


def openhands_llm(files):
    """1 step = 1 tool call (trừ `finish`) trong lịch sử `messages` của log gọi LLM có nhiều tool call nhất (amendment 1).

    Log mới nhất có thể đã bị tóm tắt (condensation). Nhiều log dài bằng nhau nhưng khác nội dung (artifact chứa nhiều
    lần chạy) thì không biết lần nào được gán nhãn → LookupError.
    """
    logs = [json.loads(files[n]) for n in files if LOG.search(n)]
    if not logs:
        raise LookupError("không có log gọi LLM")
    n = max(len(tool_calls(d["messages"])) for d in logs)
    best = [d for d in logs if len(tool_calls(d["messages"])) == n]
    sig = {tuple((tc["function"]["name"], tc["function"].get("arguments")) for _, tc in tool_calls(d["messages"])) for d in best}
    if len(sig) > 1:
        raise LookupError(f"{len(sig)} lần chạy dài bằng nhau ({n} tool call)")
    msgs = best[0]["messages"]
    results = {m.get("tool_call_id"): text(m.get("content")) for m in msgs if m.get("role") == "tool"}
    user = next((m for m in msgs if m.get("role") == "user"), {})
    steps, turns = [], {}
    for m, tc in tool_calls(msgs):
        name = tc["function"]["name"]
        a = loads(tc["function"].get("arguments") or "{}") or {}
        obs = results.get(tc.get("id"), "")
        code = re.search(r"\[The command completed with exit code (-?\d+)\.\]", obs)
        first = id(m) not in turns
        s = Step(LLM_KIND.get(name, "other"), name, obs=obs, turn=turns.setdefault(id(m), len(turns)),
                 message="\n".join(x for x in (text(m.get("content")) if first else "", a.pop("thought", "")) if x),
                 exit_code=int(code.group(1)) if code and code.group(1) != "-1" else None)
        if name == "str_replace_editor":
            s.kind = "view" if a.get("command") == "view" else "edit"
        if name == "execute_bash" and a.get("is_input") in (True, "true"):
            s.kind = "input"
        if name in ("execute_bash", "execute_ipython_cell"):
            s.command = a.get("command" if name == "execute_bash" else "code", "")
        else:
            s.args = a
        steps.append(s)
    return task_text(text(user.get("content"))), steps


TRAJECTORY = re.compile(r"(traj\.json|\.traj|/events/\d+\.json|episode-\d+/(response|prompt)\.txt|-\d{9,}\.\d+\.json|/agent-logs/[0-9a-f-]+\.json)$")  # Phần A §2, §8


def read_artifact(path):
    """{tên file: bytes} của các file trajectory trong artifact .tar.zst; mọi file khác (kết quả test, nhãn...) không được đọc."""
    import pyarrow as pa  # chỉ cần khi đọc dữ liệu offline

    with pa.CompressedInputStream(pa.OSFile(str(path)), "zstd") as s, tarfile.open(fileobj=s, mode="r|") as tf:
        return {m.name: tf.extractfile(m).read() for m in tf if m.isfile() and TRAJECTORY.search(m.name)}


def parse(agent, bench, files):
    """(mô tả task, danh sách Step) từ các file trajectory của một run; không có file trajectory → LookupError."""
    if agent == "mini-SWE-agent":
        return mini(files)
    if agent == "SWE-agent":
        return sweagent(files)
    if agent == "Terminus2":
        return terminus2(files)
    if agent == "OpenHands" and bench == "TB":
        return openhands_events(files)
    if agent == "OpenHands" and bench == "SWE":
        return openhands_llm(files)
    raise ValueError(f"scaffold không hỗ trợ: {agent}/{bench}")
