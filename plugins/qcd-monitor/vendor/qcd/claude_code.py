"""Bộ chuyển đổi Claude Code → Step (amendment 5 G2), dùng chung cho payload hook và transcript JSONL. Chỉ thư viện chuẩn.

Thiết kế: docs/2026-10-26-p5-design/design.md §4.4, §5.2.
"""
import json
import re

# Văn bản kết quả của call không chạy (G2). REJECTED gồm mọi loại; dùng cho is_prompt và data card.
REJECTED = re.compile(r"\A\s*(?:<tool_use_error>|\[Request interrupted by user|The user doesn't want to proceed|Permission to use \S+ has been denied"
                      r"|Permission for this tool use was denied)")
# Bị ngắt: lô của hook không có (spike) → bỏ ở cả hai đường
INTERRUPTED = re.compile(r"\A\s*(?:\[Request interrupted by user|The user doesn't want to proceed)")
# Bị chặn trước khi chạy (lỗi kiểm đầu vào, từ chối quyền): lô của hook có (spike) → giữ làm step lỗi (G2; user chọn 2026-10-27)
BLOCKED = re.compile(r"\A\s*(?:<tool_use_error>|Permission to use \S+ has been denied|Permission for this tool use was denied)")


def text_of(content):
    """content của tool_result / tool_response: chuỗi, hoặc mảng block → chuỗi; block không phải text → "[<type>]"."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(b.get("text", "") if isinstance(b, dict) and b.get("type") == "text"
                         else f"[{b.get('type', '?') if isinstance(b, dict) else '?'}]" for b in content)
    return "" if content is None else json.dumps(content, ensure_ascii=False, default=str)


def _content(rec):
    return (rec.get("message") or {}).get("content")


def is_prompt(rec):
    """Prompt thật: bản ghi user ở luồng chính, không meta, không chứa tool_result, không phải dòng báo hủy."""
    if rec.get("type") != "user" or rec.get("isSidechain") or rec.get("isMeta") or rec.get("isCompactSummary"):
        return False  # tóm tắt compaction là bản ghi user nhưng hook không có UserPromptSubmit (G2)
    c = _content(rec)
    if isinstance(c, str):
        return not REJECTED.search(c)
    return (isinstance(c, list) and bool(c) and not any(isinstance(b, dict) and b.get("type") == "tool_result" for b in c)
            and not REJECTED.search(text_of(c)))  # spike: "[Request interrupted by user for tool use]" ở dạng list


def parse_transcript(lines):
    """Dòng JSONL của một session → list run {"prompt", "batches"}; lô = các tool_use cùng message.id (Claude Code ghi mỗi block một dòng).

    Bỏ bản ghi sidechain. Tool call trước prompt đầu tiên thuộc một run có prompt rỗng. Dòng hỏng → json.JSONDecodeError.
    """
    runs, calls = [], {}
    for line in lines:
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("isSidechain"):
            continue
        if is_prompt(rec):
            runs.append({"prompt": text_of(_content(rec)), "batches": [], "_ix": {}})
            continue
        c = _content(rec)
        if not isinstance(c, list):
            continue
        if not runs:
            runs.append({"prompt": "", "batches": [], "_ix": {}})
        run = runs[-1]
        for b in c:
            if not isinstance(b, dict):
                continue
            if rec.get("type") == "assistant" and b.get("type") == "tool_use":
                mid = (rec.get("message") or {}).get("id") or rec.get("uuid")
                if mid not in run["_ix"]:
                    run["_ix"][mid] = len(run["batches"])
                    run["batches"].append([])
                call = {"tool_name": b.get("name", ""), "tool_input": b.get("input") or {}, "tool_use_id": b.get("id"),
                        "text": None, "is_error": False, "interrupted": False}
                run["batches"][run["_ix"][mid]].append(call)
                calls[b.get("id")] = call
            elif rec.get("type") == "user" and b.get("type") == "tool_result" and b.get("tool_use_id") in calls:
                call = calls[b["tool_use_id"]]
                call["text"], call["is_error"] = text_of(b.get("content")), bool(b.get("is_error"))
                call["interrupted"] = bool(INTERRUPTED.search(call["text"]))
    for r in runs:
        del r["_ix"]
    return runs


from qcd.adapters import Step

KIND = {"Bash": "shell", "PowerShell": "shell", "Read": "view", "Glob": "view", "Grep": "view", "LS": "view", "NotebookRead": "view",
        "Edit": "edit", "MultiEdit": "edit", "Write": "edit", "NotebookEdit": "edit"}  # tool khác → other (G2)
EXIT = re.compile(r"\AExit code (-?\d+)")
READ_ARROW = re.compile(r"^(\s*\d+)→", re.M)


def _path(p):
    return p.replace("\\", "/") if isinstance(p, str) else ""


def to_step(tool_name, tool_input, text):
    """Một tool call → Step theo amendment 5 G2; text: nội dung tool_result mà model thấy; message luôn rỗng (S1).

    Không nhận cờ lỗi: payload PostToolBatch không có trường lỗi (spike), nên cả hai đường chỉ dựa vào văn bản.
    Call bị chặn (BLOCKED): edit/view thêm tiền tố "ERROR: " (EDITOR_ERR); shell và other thành kind "rejected", exit code None, như
    adapters làm với lệnh không chạy trong dữ liệu train (p_err = 1, không trích thao tác sửa) (G2; I1, N1).
    """
    a = tool_input if isinstance(tool_input, dict) else {}
    s = lambda k: v if isinstance(v := a.get(k), str) else None
    kind, text, command, args, code = KIND.get(tool_name, "other"), (text or "").replace("\r\n", "\n"), "", {}, None
    blocked = bool(BLOCKED.match(text))
    if kind == "shell":
        command, m = s("command") or "", EXIT.match(text)
        code = int(m.group(1)) if m else 0
    elif tool_name == "Write":
        args = {"command": "create", "path": _path(a.get("file_path")), "file_text": s("content")}
    elif tool_name == "Edit":
        args = {"command": "str_replace", "path": _path(a.get("file_path")), "old_str": s("old_string"), "new_str": s("new_string")}
    elif tool_name == "MultiEdit":
        es = [e for e in a.get("edits") or [] if isinstance(e, dict)]
        j = lambda k: "\n".join(e[k] for e in es if isinstance(e.get(k), str))
        args = {"command": "str_replace", "path": _path(a.get("file_path")), "old_str": j("old_string"), "new_str": j("new_string")}
    elif tool_name == "NotebookEdit":
        args = {"command": "edit", "path": _path(a.get("notebook_path")), "new_str": s("new_source")}
    elif kind == "view":
        args = {"command": "view", "path": _path(a.get("file_path") or a.get("path") or a.get("notebook_path"))}
        command = " ".join(x for x in (tool_name, s("pattern")) if x)
        if tool_name == "Read":
            text = READ_ARROW.sub(r"\1\t", text)
    else:
        args = {k: v for k, v in a.items() if isinstance(v, (str, int, float, bool))}
    if blocked and kind in ("edit", "view"):
        text = "ERROR: " + text
    elif blocked:
        kind, code = "rejected", None
    return Step(kind, tool_name, command, args, "", text, code, 0)


def runs_steps(runs):
    """Run (parse_transcript, tb2.main_runs) → [(prompt, [Step])]; bỏ call bị ngắt hoặc không có kết quả, giữ call bị chặn làm step lỗi (G2)."""
    return [(r["prompt"], [to_step(c["tool_name"], c["tool_input"], c["text"]) for b in r["batches"] for c in b
                           if c["text"] is not None and not c["interrupted"]]) for r in runs]


def transcript_steps(lines):
    """Một session JSONL → [(prompt, [Step])]."""
    return runs_steps(parse_transcript(lines))


def batch_steps(payload):
    """Payload PostToolBatch → [Step] theo thứ tự tool_calls; cùng quy tắc bỏ như transcript_steps (bỏ call bị ngắt)."""
    out = []
    for c in payload.get("tool_calls") or []:
        text = text_of(c.get("tool_response"))
        if not INTERRUPTED.search(text):
            out.append(to_step(c.get("tool_name", ""), c.get("tool_input"), text))
    return out
