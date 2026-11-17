"""Hook của plugin qcd-monitor (design P5 §4): UserPromptSubmit mở run mới; PostToolBatch cập nhật CUSUM và báo động theo chế độ.

Fail-open: mọi lỗi → ghi log, thoát 0, stdout rỗng; riêng sự cố làm mất giám sát (model, ruff) có thông báo systemMessage một lần mỗi phiên.
Chỉ thư viện chuẩn. Log không ghi nội dung code, lệnh hay prompt.
"""
import time

T0 = time.perf_counter()
import datetime
import hashlib
import json
import marshal
import os
import pathlib
import re
import shutil
import sys
import tempfile
import textwrap

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vendor" if (ROOT / "vendor").exists() else ROOT.parent))  # bản xuất; trong repo: gốc repo

import subprocess  # noqa: E402

from qcd import claude_code, gbdt, textops  # noqa: E402
from qcd.detect import tie_u  # noqa: E402
from qcd.features import FEATURES, Tracker  # noqa: E402

T_IMPORT = time.perf_counter()
INF = float("inf")
STATE_TTL_DAYS = 7


NOTES = {"ruff": "qcd-monitor: không dùng được ruff 0.15.12 (thiếu hoặc khác bản); 5 đặc trưng lint bị bỏ trống, ngưỡng không còn khớp với lúc hiệu chỉnh.",
         "model": "qcd-monitor: không nạp được model; plugin không chấm điểm và không báo động. Xem mục Xử lý sự cố trong README."}


def _model_dir(env):
    return pathlib.Path(env.get("QCD_MODEL_DIR") or ROOT / "model")


def _trees(env, data, meta):
    """Model compact. m1.json không phụ thuộc bản Python; bản marshal do chính Python đang chạy ghi vào cache để nạp nhanh (≈2 ms so với ≈14 ms)."""
    cache = data / "cache" / f"m1-{meta['sha256'][:16]}-{sys.implementation.cache_tag}.bin"
    try:
        return marshal.loads(cache.read_bytes())
    except (OSError, ValueError, EOFError, TypeError):  # chưa có hoặc hỏng → đọc lại JSON
        pass
    trees = json.loads((_model_dir(env) / "m1.json").read_text(encoding="utf-8"))
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_suffix(f".{os.getpid()}.tmp")  # tên riêng: hai hook ghi cùng lúc không trộn byte
        tmp.write_bytes(marshal.dumps(trees))
        os.replace(tmp, cache)
    except OSError:
        pass
    return trees


class _Subagent(Exception):
    pass


def _write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj), encoding="utf-8")
    os.replace(tmp, path)  # ghi trọn hoặc không ghi


def _load_state(path):
    try:
        st = json.loads(path.read_text(encoding="utf-8"))
        st["tracker"] = Tracker.from_dict(st["tracker"])
        st["run"], st["k"], st["w"], st["alarm"]  # khóa bắt buộc; thiếu → bắt đầu mới
        return st
    except Exception:
        return None


def _purge_old(sdir, keep):
    """Xóa state không cập nhật quá STATE_TTL_DAYS ngày (#28), trừ file của phiên hiện tại."""
    cutoff = time.time() - STATE_TTL_DAYS * 86400
    for f in [*sdir.glob("*.json"), *sdir.glob("*.tmp")]:  # .tmp: bản ghi dở của tiến trình bị ngắt
        try:  # một file lỗi (đang khóa, bị xóa giữa chừng) không được chặn việc mở run mới
            if f != keep and f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            continue


def _new_state(run, prompt, old=None):
    st = {"run": run, "k": 0, "w": 0.0, "alarm": None, "tracker": Tracker(prompt)}
    for key in ("ruff_ok", "warned"):  # kiểm ruff và thông báo sự cố một lần mỗi phiên
        if old and key in old:
            st[key] = old[key]
    return st


def _note(st, key):
    """→ thông báo cho người dùng nếu sự cố `key` chưa được báo trong phiên, rồi đánh dấu đã báo."""
    if key in st.setdefault("warned", []):
        return ""
    st["warned"].append(key)
    return NOTES[key]


def _ruff(st):
    """Design §4.4: thiếu ruff hoặc khác RUFF_VERSION → xử lý như thiếu ruff (5 feature lint để trống). → ruff dùng được cho lô này không."""
    ok = st.get("ruff_ok")
    if ok is None:
        try:
            ok = st["ruff_ok"] = textops.RUFF_VERSION in subprocess.run([textops.RUFF, "--version"], capture_output=True, text=True, timeout=10).stdout
        except FileNotFoundError:
            ok = st["ruff_ok"] = False
        except (OSError, subprocess.TimeoutExpired):  # lỗi tạm thời (ví dụ ruff khởi động chậm): lô này coi như thiếu ruff, lô sau kiểm lại
            ok = False
    if not ok:
        textops.ruff_findings = lambda code: None
    return ok


def prelint(steps):
    """Gộp mọi chuỗi mà lint_counts sẽ lint cho các step của lô vào một tiến trình ruff (spike: user chọn tối ưu, giữ 49 feature).

    Kết quả vào cache theo chuỗi code, đặt chồng lên textops.ruff_findings; chuỗi ngoài cache, hoặc ruff lỗi → ruff_findings gốc như cũ.
    """
    codes = []
    for step in steps:
        for o in textops.edit_ops(step):
            if o.path.endswith(".py") and o.new is not None and o.mode in ("whole", "fragment"):
                codes.append(textwrap.dedent(o.new) if o.mode == "fragment" else o.new)
                if o.old is not None:
                    codes.append(textwrap.dedent(o.old))
    codes = list(dict.fromkeys(codes))
    if not codes:
        return
    d = tempfile.mkdtemp()
    try:
        paths = [os.path.join(d, f"f{i}.py") for i in range(len(codes))]
        for path, code in zip(paths, codes):
            with open(path, "wb") as fh:
                fh.write(code.encode("utf-8"))  # bytes: không đổi \n → \r\n, như ruff_findings
        r = subprocess.run([textops.RUFF, "check", "--isolated", "--no-cache", "--select", "E9,F,S", "--output-format", "json", *paths],
                           capture_output=True, timeout=10)
        key = lambda x: os.path.normcase(os.path.abspath(x))
        by = {key(x): [] for x in paths}
        for x in json.loads(r.stdout):
            by[key(x["filename"])].append((x.get("code") or "", x["location"]["row"]))
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        return
    finally:
        shutil.rmtree(d, ignore_errors=True)
    cache, orig = {c: by[key(x)] for c, x in zip(codes, paths)}, textops.ruff_findings
    textops.ruff_findings = lambda code: cache[code] if code in cache else orig(code)


def status_text(st, meta, mode, alpha):
    h = meta["thresholds"][alpha][0]
    return (f"qcd-monitor status: run {st['run']}, {st['k']} step, W = {st['w']:.3f}, h = {h:.3f} (α = {alpha}), "
            f"báo động: {'step ' + str(st['alarm']) if st['alarm'] else 'chưa'}, chế độ {mode}, model {meta['variant']} {meta['sha256'][:12]}"
            + "".join(f"\n{NOTES[k]}" for k in st.get("warned", [])))


def main(stdin_text, env):
    """→ stdout cho Claude Code; ghi state và một dòng log."""
    if not env.get("CLAUDE_PLUGIN_DATA"):
        return ""
    data = pathlib.Path(env["CLAUDE_PLUGIN_DATA"])
    rec = {"ts": datetime.datetime.now().isoformat(timespec="milliseconds"), "event": None, "session": None, "run": None, "k": None,
           "ws": [], "alarm": None, "mode": env.get("CLAUDE_PLUGIN_OPTION_MODE", "observe"), "alpha": env.get("CLAUDE_PLUGIN_OPTION_ALPHA", "0.1"),
           "model_sha": None, "t_import_ms": (T_IMPORT - T0) * 1000, "error": ""}
    out = ""
    try:
        p = json.loads(stdin_text)
        rec["event"], sid = p.get("hook_event_name"), str(p.get("session_id", ""))
        rec["session"] = sid
        if p.get("agent_id"):  # lô của subagent: hook thoát sớm (amendment 5 G6), không đọc hay ghi state
            raise _Subagent
        spath = data / "state" / f"{hashlib.sha256(sid.encode()).hexdigest()[:16]}.json"
        t = time.perf_counter()
        meta = json.loads((_model_dir(env) / "meta.json").read_text(encoding="utf-8"))
        rec["model_sha"], mode, alpha = meta["sha256"], rec["mode"], rec["alpha"]
        st = _load_state(spath)
        rec["t_load_ms"] = (time.perf_counter() - t) * 1000  # đọc meta và state (I4)
        if rec["event"] == "UserPromptSubmit":
            prompt = str(p.get("prompt", ""))
            _purge_old(spath.parent, spath)
            if re.match(r"\s*/(?:qcd-monitor:)?qcd-status\b", prompt):
                st = st or _new_state(1, "")
                out = json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": status_text(st, meta, mode, alpha)}})
            else:
                st = _new_state((st or {"run": 0})["run"] + 1, prompt, st)
        elif rec["event"] == "PostToolBatch":
            st = st or _new_state(1, "")
            t = time.perf_counter()
            if env.get("QCD_BASELINE_B0") == "1":  # chỉ để đo độ trễ của B0 (design §5.5): W = số step của run, không nạp model
                rec["b0"] = True
                for _ in claude_code.batch_steps(p):
                    st["k"] += 1
                    st["w"] = float(st["k"])
                    rec["ws"].append(st["w"])
                p = {}
            steps, note = claude_code.batch_steps(p) if p else [], ""
            if p:
                if _ruff(st):
                    prelint(steps)
                elif st.get("ruff_ok") is False:  # chỉ khi chắc chắn thiếu/khác bản, không báo lỗi tạm thời
                    note = _note(st, "ruff")
            rec["t_lint_ms"] = (time.perf_counter() - t) * 1000
            t = time.perf_counter()
            try:
                trees = _trees(env, data, meta) if p else None
            except Exception as e:  # vẫn fail-open, nhưng ghi state để chỉ báo một lần mỗi phiên
                rec["error"], trees, steps, note = type(e).__name__, None, [], _note(st, "model")
            rec["t_model_ms"] = (time.perf_counter() - t) * 1000
            h = meta["thresholds"][alpha]
            if env.get("QCD_TEST_FORCE_ALARM") == "1":  # chỉ để thử warn/strict (design §4.4); ghi vào log
                h, rec["force_alarm"] = [-1.0, 0.0], True
            u, c, tf, ts, newly = tie_u(0, f"{sid}:{st['run']}"), meta["c"], 0.0, 0.0, None
            for step in steps:
                t1 = time.perf_counter()
                f = st["tracker"].update(step)
                t2 = time.perf_counter()
                ell = max(min(gbdt.margin_c(trees, [f[k] for k in FEATURES]) - meta["logit_pi"], c), -c)
                st["w"], st["k"] = max(0.0, st["w"] + ell - meta["delta"]), st["k"] + 1
                tf, ts = tf + t2 - t1, ts + time.perf_counter() - t2
                rec["ws"].append(st["w"])
                if st["alarm"] is None and (st["w"], u) > tuple(h):
                    st["alarm"] = newly = st["k"]
            rec["t_feat_ms"], rec["t_score_ms"] = tf * 1000, ts * 1000
            msg = f"qcd-monitor: báo động ở step {newly} của run (W = {st['w']:.2f} > h = {h[0]:.2f}, α = {alpha})"
            obj = {}
            if newly and mode == "warn":
                obj = {"systemMessage": msg, "hookSpecificOutput": {"hookEventName": "PostToolBatch", "additionalContext": msg}}
            elif newly and mode == "strict":
                obj = {"continue": False, "stopReason": msg}
            if note:  # thông báo sự cố chỉ cho người dùng, không vào ngữ cảnh agent
                obj["systemMessage"] = note + ("\n" + obj["systemMessage"] if "systemMessage" in obj else "")
            out = json.dumps(obj) if obj else ""
        if st is not None:
            t = time.perf_counter()
            _write(spath, {**st, "tracker": st["tracker"].to_dict()})
            rec["t_state_ms"] = (time.perf_counter() - t) * 1000
            rec.update(run=st["run"], k=st["k"], alarm=st["alarm"])
    except _Subagent:
        pass
    except Exception as e:  # fail-open (S2)
        rec["error"], out = type(e).__name__, ""
    rec["t_total_ms"] = (time.perf_counter() - T0) * 1000
    try:
        log = data / "log" / f"{datetime.date.today().isoformat()}.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError:
        pass
    return out


if __name__ == "__main__":
    try:
        os.chdir(os.path.dirname(os.path.abspath(__file__)))  # Windows tìm ruff.exe ở thư mục hiện tại trước PATH
        sys.stdout.write(main(sys.stdin.buffer.read().decode("utf-8", "replace"), os.environ))
    except Exception:  # kể cả thiếu CLAUDE_PLUGIN_DATA
        pass
    sys.exit(0)
