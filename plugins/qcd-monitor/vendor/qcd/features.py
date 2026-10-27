"""Bộ tính feature online, dùng chung cho offline và plugin (chỉ thư viện chuẩn ở cấp module).

Feature của step k chỉ dùng step 1..k (Phần A §8). Định nghĩa: docs/2026-10-21-p2-design/design.md §2.3.
Dựng bảng feature (offline, không đọc nhãn):
  python -I -X utf8 qcd/features.py data/processed/codetracebench-aa213b8
"""
import hashlib
import json
import math
import numbers
import os
import re
import sys

if __name__ == "__main__":  # -I không thêm thư mục nào vào sys.path; thêm gốc repo (đáng tin) để import qcd
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qcd.textops import (SCREEN_MARKER, action_key, edit_ops, editor_error, error_signature, exec_error, is_test_path, lint_counts,
                         scan_text, state_head, test_kind, test_summary, tokens)

KINDS = ("shell", "input", "edit", "view", "python", "think", "finish", "rejected", "other")
FEATURES_P = tuple(f"p_kind_{k}" for k in KINDS) + ("p_err", "p_err_streak", "p_err_rate5", "p_rep_cmd20", "p_rep_consec",
                                                    "p_rep_err20", "p_cycle10", "p_obs_len", "p_cmd_len")
FEATURES_AEDIT = ("a_n_edits", "a_n_py", "a_whole", "a_syn", "a_flake", "a_sec", "a_lint_delta", "a_add_lines", "a_del_lines",
                  "a_reedit20", "a_self_over", "a_revert", "a_test_file", "a_assert_drop", "a_skip_add", "a_test_rm",
                  "a_since_edit", "a_edits_w10", "a_lint_w10")
CONTENT_DERIVED = ("a_whole", "a_syn", "a_flake", "a_sec", "a_lint_delta", "a_add_lines", "a_del_lines", "a_self_over",
                   "a_revert", "a_assert_drop", "a_skip_add", "a_lint_w10")  # feature † — để trống ở mini-SWE-agent (#11, #13)
FEATURES_ATEST = ("t_fw", "t_adhoc", "t_fail", "t_npass", "t_nfail", "t_regress", "t_fail_streak", "t_since_pass")
FEATURES_R = ("r_overlap", "r_overlap_msg", "r_cov10", "r_path_task")
FEATURES = FEATURES_P + FEATURES_AEDIT + FEATURES_ATEST + FEATURES_R  # 49
PROBE_RUN = "results/p2-ruff-fragments/20261021-103733_seed0"
TOKEN_CHARS = 5_000  # token của kênh bám yêu cầu lấy từ tối đa 5.000 ký tự của action / message
ASSERT = re.compile(r"^[ \t]*assert\b|\bself\.assert\w*\(", re.M)  # [ \t]*: \s* dưới re.M bậc hai trên dòng trống
SKIP = re.compile(r"@pytest\.mark\.(?:skip|xfail)|\bpytest\.(?:skip|xfail)\(|@unittest\.skip|\bself\.skipTest\(|\.skip\(")
WIN, PREV_OBS_MAX, SINCE_CAP = 20, 20_000, 50
HASH_CAP = 5_000  # ponytail: giữ 5.000 hash dòng gần nhất mỗi path cho a_self_over, đủ cho run benchmark; tăng nếu plugin cần
def lp(x):
    return math.log1p(min(x, 1e18))  # số đếm cực lớn từ đầu ra lạ không làm tràn float


def _h(text):
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _long_lines(text):
    return [l.strip() for l in text.splitlines() if len(l.strip()) >= 8]


def _sum_or_none(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) if xs else None


def _exit(step):
    return int(step.exit_code) if isinstance(step.exit_code, numbers.Integral) else None


def action_text(step):
    return step.command or (json.dumps(step.args, sort_keys=True, ensure_ascii=False, default=str) if step.args else "")


class Tracker:
    """Trạng thái online của một run; chỉ gồm list/số/chuỗi để tuần tự hóa được (plugin lưu giữa các lần gọi hook)."""

    def __init__(self, task=""):
        self.task = task
        self.prev_obs = ""  # observation không rỗng gần nhất, cho quy tắc ảnh chụp màn hình
        self.errs, self.keys, self.sigs, self.heads = [], [], [], []  # cửa sổ 5 / 20 / 20 / 10 step
        self.streak, self.consec, self.last_key = 0, 0, None
        self.k, self.last_edit = 0, None
        self.added, self.olds, self.whole_counts = {}, {}, {}  # theo path: hash dòng agent thêm; hash old; [assert, skip] toàn văn
        self.op_paths, self.edit_counts, self.lint_w = [], [], []  # cửa sổ 20 / 10 / 10 step
        self.passed_keys, self.t_streak, self.last_pass = [], 0, None
        self.task_terms, self.act_tokens = sorted(tokens(task)), []  # cửa sổ 10 step

    def to_dict(self):
        return json.loads(json.dumps(self.__dict__))  # ảnh chụp: không dùng chung list/dict với tracker đang chạy

    @classmethod
    def from_dict(cls, d):
        t = cls.__new__(cls)
        t.__dict__.update(json.loads(json.dumps(d)))
        return t

    def _atest(self, step, text, key):
        kind = test_kind(step.command) if step.kind in ("shell", "python", "input") else None
        summ = test_summary(text) if kind else None
        code, fail = _exit(step), None
        if kind and summ:  # có dòng tổng kết: 0 passed và 0 failed → không biết kết quả
            fail = 1 if summ[1] > 0 else 0 if summ[0] > 0 else None
        elif kind:
            fail = int(code != 0) if code is not None else None
        regress = None if fail is None else int(fail == 1 and key in self.passed_keys)
        if fail is not None:
            self.t_streak = self.t_streak + 1 if fail else 0
        if fail == 0:
            self.last_pass, self.passed_keys = self.k, (self.passed_keys + [key])[-WIN:]
        return dict(t_fw=int(kind == "framework"), t_adhoc=int(kind == "adhoc"), t_fail=fail,
                    t_npass=lp(summ[0]) if summ else None, t_nfail=lp(summ[1]) if summ else None, t_regress=regress,
                    t_fail_streak=self.t_streak, t_since_pass=None if self.last_pass is None else min(self.k - self.last_pass, SINCE_CAP))

    def _adherence(self, step, ops):
        a = step.args if isinstance(step.args, dict) else {}
        at = tokens(" ".join([step.command or ""] + [str(a.get(x) or "") for x in ("path", "old_str", "new_str", "file_text")])[:TOKEN_CHARS])
        mt, terms = tokens((step.message or "")[:TOKEN_CHARS]), set(self.task_terms)
        self.act_tokens = (self.act_tokens + [sorted(at)])[-10:]
        paths = [o.path for o in ops if o.path] + ([str(a["path"])] if a.get("path") else [])
        base = [p.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1] for p in paths]
        names = {w.strip(".-") for w in re.findall(r"[\w.\-]+", self.task)} - {""} if base else set()  # token của task, bỏ dấu câu ở hai đầu
        return dict(r_overlap=len(at & terms) / len(at) if at else None, r_overlap_msg=len(mt & terms) / len(mt) if mt else None,
                    r_cov10=len({t for ts in self.act_tokens for t in ts} & terms) / len(terms) if terms else None,
                    r_path_task=int(any(b.strip(".-") in names for b in base)) if base else None)  # basename bỏ . - như token: .gitignore

    def _aedit(self, ops):
        py =[o for o in ops if o.path.endswith(".py") and o.new is not None and o.mode in ("whole", "fragment")]
        lint = [lint_counts(o.new, o.mode == "fragment") for o in py]
        ok = [x for x in lint if x is not None]
        deltas = []
        for o, x in zip(py, lint):
            y = lint_counts(o.old, True) if o.old is not None and x is not None else None
            if y is not None:
                deltas.append(sum(x) - sum(y))
        news, olds = [o for o in ops if o.new is not None], [o for o in ops if o.old is not None]
        recent = [p for ps in self.op_paths for p in ps]
        drop, add = [], []
        for o in (o for o in news if is_test_path(o.path)):
            prev = (len(ASSERT.findall(o.old)), len(SKIP.findall(o.old))) if o.old is not None else \
                self.whole_counts.get(o.path) if o.mode == "whole" else None
            if prev is not None:
                drop.append(max(0, prev[0] - len(ASSERT.findall(o.new))))
                add.append(max(0, len(SKIP.findall(o.new)) - prev[1]))
        if ops:
            self.last_edit = self.k
        total = [sum(x) for x in ok]
        self.edit_counts, self.lint_w = (self.edit_counts + [len(ops)])[-10:], (self.lint_w + [sum(total) if ok else None])[-10:]
        f = dict(a_n_edits=len(ops), a_n_py=sum(o.path.endswith(".py") for o in ops), a_whole=int(any(o.mode == "whole" for o in py)) if py else None,
                 a_syn=sum(x[0] for x in ok) if ok else None, a_flake=sum(x[1] for x in ok) if ok else None,
                 a_sec=sum(x[2] for x in ok) if ok else None, a_lint_delta=sum(deltas) if deltas else None,
                 a_add_lines=lp(sum(len(o.new.splitlines()) for o in news)) if news else None,
                 a_del_lines=lp(sum(len(o.old.splitlines()) for o in olds)) if olds else None,
                 a_reedit20=lp(max(recent.count(o.path) if o.path else 0 for o in ops)) if ops else None,
                 a_self_over=int(any(not set(map(_h, _long_lines(o.old))).isdisjoint(self.added.get(o.path, ())) for o in olds)) if olds else None,
                 a_revert=int(any(o.mode == "undo" or (o.new is not None and _h(o.new) in self.olds.get(o.path, ())) for o in ops)) if ops else None,
                 a_test_file=int(any(is_test_path(o.path) for o in ops)) if ops else None,
                 a_assert_drop=sum(drop) if drop else None, a_skip_add=sum(add) if add else None,
                 a_test_rm=int(any(o.mode == "delete" and is_test_path(o.path) for o in ops)),
                 a_since_edit=None if self.last_edit is None else min(self.k - self.last_edit, SINCE_CAP),
                 a_edits_w10=sum(self.edit_counts), a_lint_w10=_sum_or_none(self.lint_w))
        for o in news:  # cập nhật trạng thái sau khi tính: feature chỉ thấy các thao tác trước
            self.added[o.path] = (self.added.get(o.path, []) + [_h(l) for l in _long_lines(o.new)])[-HASH_CAP:]
            if o.mode == "whole" and is_test_path(o.path):
                self.whole_counts[o.path] = [len(ASSERT.findall(o.new)), len(SKIP.findall(o.new))]
        for o in olds:
            self.olds[o.path] = (self.olds.get(o.path, []) + [_h(o.old)])[-HASH_CAP:]
        self.op_paths = (self.op_paths + [[o.path for o in ops if o.path]])[-WIN:]
        return f

    def _err(self, step, text):
        """p_err: exit code nếu có; không thì mẫu lỗi thực thi (shell/python/input) hoặc lỗi editor (edit/view)."""
        if step.kind in ("shell", "python", "input"):
            code = _exit(step)
            return int(code != 0) if code is not None else (int(exec_error(text)) if step.obs else None)
        if step.kind in ("edit", "view"):
            return int(editor_error(text)) if step.obs else None
        return 1 if step.kind == "rejected" else None

    def update(self, step):
        self.k += 1
        kind = step.kind if step.kind in KINDS else "other"
        f = {f"p_kind_{k}": float(kind == k) for k in KINDS}
        text = scan_text(step.obs, self.prev_obs, step.command)
        err = self._err(step, text)
        if err is not None:
            self.streak = self.streak + 1 if err else 0
        self.errs = (self.errs + [err])[-5:]
        known = [e for e in self.errs if e is not None]
        key = action_key(step)
        self.consec = self.consec + 1 if key == self.last_key else 1
        sig = None
        if err:  # lỗi không có dòng khớp mẫu (vd. chỉ có exit code) → chữ ký theo exit code / kind
            code = _exit(step)
            sig = error_signature(text) or (f"exit:{code}" if code is not None else f"kind:{kind}")
        self.heads = (self.heads + [state_head(step)])[-10:]
        f.update(p_err=err, p_err_streak=self.streak, p_err_rate5=sum(known) / len(known) if known else None,
                 p_rep_cmd20=lp(self.keys.count(key)), p_rep_consec=self.consec,
                 p_rep_err20=None if err is None else lp(self.sigs.count(sig)) if err else 0.0,
                 p_cycle10=1 - len(set(self.heads)) / len(self.heads) if len(self.heads) >= 2 else None,
                 p_obs_len=lp(len(step.obs or "")), p_cmd_len=lp(len(action_text(step))))
        self.keys, self.sigs, self.last_key = (self.keys + [key])[-WIN:], (self.sigs + [sig])[-WIN:], key
        if step.obs:  # phần đem trừ của ảnh chụp màn hình: màn hình gần nhất + output mới từ sau nó (#15)
            tail = step.obs[-PREV_OBS_MAX:]
            self.prev_obs = (tail if SCREEN_MARKER in step.obs or not self.prev_obs else self.prev_obs + "\n" + tail)[-PREV_OBS_MAX:]
        ops = edit_ops(step)
        f.update(self._aedit(ops))
        f.update(self._atest(step, text, key))
        f.update(self._adherence(step, ops))
        return f


def run_features(task, steps):
    t = Tracker(task)
    return [t.update(s) for s in steps]


def steps_from_frame(df):
    """Các dòng steps.parquet của một run (đã sắp theo k) → list Step; exit code thiếu → None."""
    from qcd.adapters import Step
    out = []
    for r in df.itertuples(index=False):
        try:
            code = int(r.exit_code)
        except (TypeError, ValueError):
            code = None
        args = json.loads(r.args) if isinstance(r.args, str) and r.args else {}
        out.append(Step(r.kind, r.tool, r.command or "", args if isinstance(args, dict) else {}, r.message or "", r.obs or "", code, int(r.turn)))
    return out


def main(processed_dir, results_root="results"):
    """Ghi features.parquet (traj_id, k, 49 feature) cho mọi step và results/p2-features/<ts>_seed0. Không đọc nhãn."""
    import datetime
    import pathlib
    import platform
    import subprocess
    import time

    import pandas as pd
    import pyarrow

    from qcd.textops import FRAGMENT_UNSAFE, RUFF, RUFF_VERSION

    runs = pd.read_parquet(os.path.join(processed_dir, "runs.parquet"), columns=["traj_id", "agent", "task"]).set_index("traj_id")
    steps = pd.read_parquet(os.path.join(processed_dir, "steps.parquet")).sort_values(["traj_id", "k"], kind="stable")
    rows, times = [], []
    for tid, g in steps.groupby("traj_id", sort=False):
        agent, t = runs.at[tid, "agent"], Tracker(runs.at[tid, "task"] or "")
        for k, step in zip(g.k, steps_from_frame(g)):
            t0 = time.perf_counter()
            f = t.update(step)
            times.append((agent, time.perf_counter() - t0))
            if agent == "mini-SWE-agent":  # #11/#13: feature suy từ nội dung sửa để trống
                f.update(dict.fromkeys(CONTENT_DERIVED))
            rows.append({"traj_id": tid, "k": int(k), **f})
    df = pd.DataFrame(rows, columns=["traj_id", "k", *FEATURES])
    df[list(FEATURES)] = df[list(FEATURES)].astype(float)
    out = os.path.join(processed_dir, "features.parquet")
    df.to_parquet(out, index=False)

    tab, by = [], df.assign(agent=df.traj_id.map(runs.agent))
    for scope, x in [("all", by)] + list(by.groupby("agent")):
        for c in FEATURES:
            tab += [{"section": "feature", "scope": scope, "metric": f"{c}:missing", "value": round(float(x[c].isna().mean()), 4)},
                    {"section": "feature", "scope": scope, "metric": f"{c}:mean", "value": round(float(x[c].mean()), 4) if x[c].notna().any() else None}]
    tm = pd.DataFrame(times, columns=["agent", "s"])
    for scope, x in [("all", tm)] + list(tm.groupby("agent")):
        tab += [{"section": "time_ms", "scope": scope, "metric": f"p{q}", "value": round(float(x.s.quantile(q / 100)) * 1000, 3)} for q in (50, 95, 99)]
    res = pathlib.Path(results_root, "p2-features", datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + "_seed0")
    res.mkdir(parents=True)
    pd.DataFrame(tab).to_csv(res / "table.csv", index=False)
    root = pathlib.Path(__file__).resolve().parents[1]  # git chạy ở gốc repo: pathspec "qcd" không phụ thuộc cwd
    git = lambda *a: subprocess.run(["git", *a], capture_output=True, text=True, cwd=root).stdout.strip()
    sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
    ruff_v = subprocess.run([RUFF, "--version"], capture_output=True, text=True).stdout.strip()
    (res / "params.json").write_text(json.dumps({
        "processed": str(processed_dir), "rows": len(df), "features": len(FEATURES), "ruff": ruff_v, "ruff_pinned": RUFF_VERSION,
        "fragment_unsafe": sorted(FRAGMENT_UNSAFE), "probe_run": PROBE_RUN, "blank_content_derived": "mini-SWE-agent",
        "inputs_sha256": {n: sha(os.path.join(processed_dir, n)) for n in ("runs.parquet", "steps.parquet")},
        "outputs_sha256": {"features.parquet": sha(out)},
        "code_commit": git("rev-parse", "HEAD"), "code_dirty": bool(git("status", "--porcelain", "qcd"))}, indent=2, ensure_ascii=False), encoding="utf-8")
    (res / "env.txt").write_text(f"{platform.platform()}\n{platform.processor()}\ncpu_count={os.cpu_count()}\npython={sys.version}\n"
                                 f"pandas={pd.__version__}\npyarrow={pyarrow.__version__}\n{ruff_v}\n", encoding="utf-8")
    return res


if __name__ == "__main__":
    print(main(sys.argv[1]))
