"""Hàm thuần trên văn bản của step, dùng chung cho bộ tính feature offline và plugin (chỉ thư viện chuẩn).

Định nghĩa theo docs/2026-10-21-p2-design/design.md §2.1–2.2.
"""
import hashlib
import json
import posixpath
import re
import shlex
import subprocess
import textwrap
from collections import Counter, namedtuple

Op = namedtuple("Op", "path old new mode")  # mode: whole | fragment | unknown | undo | delete
SCREEN_MARKER = "Current Terminal Screen"  # Terminus2: observation là ảnh chụp toàn màn hình
PROMPT = re.compile(r"(?:\([^)\n]*\) ?)?[^\s@]+@[^\s:]+:[^\n]*?[#$] (\S.*)")  # dòng prompt Terminus2: (venv) user@host:path# lệnh
TAIL = 10_000  # regex chỉ chạy trên 10.000 ký tự cuối của observation

# [ \t]* thay cho \s* sau ^: \s* dưới re.M vượt qua dòng trống và thành bậc hai trên chuỗi dòng trống
EXEC_ERR = re.compile(r"Traceback \(most recent call last\)|^[ \t]*[A-Za-z_][\w.]*(?:Error|Exception):|command not found"
                      r"|No such file or directory|Permission denied|^(?:[Ff]atal|[Ee]rror|ERROR):\s|Segmentation fault", re.M)
EDITOR_ERR = re.compile(r"\A\s*ERROR:|No replacement was performed|did not appear verbatim|does not exist"  # ERROR: ở đầu observation
                        r"|Your proposed edit has introduced new syntax error")
NUMBERED = re.compile(r"^[ \t]*\d+(?:\t|:).*$", re.M)  # dòng nội dung file có đánh số: cat -n (OpenHands), open (SWE-agent)
HEREDOC = re.compile(r"(?<!<)<<(?!<)-?[ \t]*(['\"]?)([A-Za-z_]\w*)\1")  # không khớp here-string <<< và phép dịch bit 1 << 3
PY_WRITE = re.compile(r"""open\(\s*['"]([^'"]+)['"]\s*,\s*['"][wa]b?\+?['"]|Path\(\s*['"]([^'"]+)['"]\s*\)\.write_(?:text|bytes)\(""")
PY_WRITE_ANY = re.compile(r"""\bopen\([^\n]*?,\s*(?:mode\s*=\s*)?['"][wa]b?\+?['"]|\.write_(?:text|bytes)\(""")  # kể cả path là biến
PYTHON = re.compile(r"(\S*/)?python[\d.]*")
SHLEX_MAX = 20_000  # dòng logic dài nhất trong dữ liệu thật: 12.472 ký tự (CodeTraceBench), 2.283 (nebius)
PUNCT, SEPARATORS = frozenset("();<>|&"), {"&&", "||", ";", "|", "&", "|&", ";;", ";&"}
WRITE_REDIR, NULL_PATHS = {">", ">>", ">|", "&>", "&>>"}, {"/dev/null", "/dev/stdout", "/dev/stderr"}
PREFIX = re.compile(r"^\s*(?:cd\s+\S+\s*(?:&&|;)\s*|sudo\s+|timeout\s+\S+\s+|\w+=\S*\s+)+")
TEST_PATH = re.compile(r"(^|/)(tests?|testing)/|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$")
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")
STOPWORDS = frozenset("""
the and for are but not you all any can had her was one our out has have him his how its may new now old see two way who
did get got let put say she too use used using with this that from they will would there their what about which when make
like time just know take into your some could them than then look only come over think also back after work first well
even want because these give most should must been being were does done each other such here more very where while both
same need needs sure check file files line lines code run runs running test tests testing name value values data error
errors output input result results true false none null self def class return import elif else try except finally pass
raise lambda yield print len str int dict list set range assert async await global cat echo grep sed awk find head tail
less more sudo bash python python3 pip install mkdir chmod touch export source which sort uniq xargs git diff status log
apply tmp usr bin home dev root app src lib testbed workspace repo exit help note let's now okay please into using via
""".split())


def _args(step):
    return step.args if isinstance(step.args, dict) else {}


def _unq(w):
    return w[1:-1] if len(w) >= 2 and w[0] == w[-1] and w[0] in "'\"" else w


def _quote_state(s, q=None):
    """Trạng thái sau khi đọc dòng s, bắt đầu từ q: None (đã đóng), "'" hoặc '"' (nháy chưa đóng), "\\" (gạch chéo ngược nối dòng)."""
    i = 0
    while i < len(s):
        c = s[i]
        if q == "'":
            q = None if c == "'" else q
        elif c == "\\":
            if i + 1 == len(s):
                return q or "\\"
            i += 1
        elif q == '"':
            q = None if c == '"' else q
        elif c in "'\"":
            q = c
        elif c == "#" and (i == 0 or s[i - 1].isspace()):  # chú thích
            break
        i += 1
    return q


def _segments(line):
    """Token shell (POSIX) của một dòng logic, theo từng lệnh con (tách ở && || ; | &); nháy lẻ hoặc dòng quá dài → tách theo khoảng trắng."""
    line = re.sub(r"(?<![\w\\])(\d+)(?=[<>])", "\\1\x01", line)  # số fd dính liền toán tử (2>f, 2>&1) được đánh dấu \x01
    try:
        if len(line) > SHLEX_MAX:  # shlex nối token từng ký tự → bậc hai theo độ dài token
            raise ValueError("dòng logic quá dài")
        lex = shlex.shlex(line, posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        toks = list(lex)
    except ValueError:
        toks = [_unq(t) for t in line.split()]
    seg = []
    for t in toks:
        if t and set(t) <= PUNCT:
            t = t.replace("(", "").replace(")", "")
            if not t:
                continue
            if t in SEPARATORS:
                yield seg
                seg = []
                continue
        seg.append(t)
    yield seg


def _parse(toks):
    """(words, ghi): words đã bỏ chuyển hướng và tiền tố sudo/VAR=/timeout; ghi = [(toán tử, đích)] của stdout."""
    words, outs, i = [], [], 0
    while i < len(toks):
        t = toks[i]
        if t and set(t) <= PUNCT and ("<" in t or ">" in t):  # chuyển hướng; token sau là đích
            fd = words.pop()[:-1] if words and words[-1].endswith("\x01") else None
            target = toks[i + 1].replace("\x01", "") if i + 1 < len(toks) else ""
            if t in WRITE_REDIR and fd in (None, "1") and target not in NULL_PATHS:
                outs.append((t, target))
            i += 2
            continue
        words.append(t)
        i += 1
    words = [w.replace("\x01", "") for w in words]
    while words:
        if words[0] == "sudo" or re.fullmatch(r"\w+=.*", words[0], re.S):
            words = words[1:]
        elif words[0] == "timeout" and len(words) > 1:
            words = words[2:]
        else:
            break
    return words, outs


def _py_ops(code):
    """Script Python mở file để ghi: path hằng chuỗi; ghi qua path là biến → một thao tác path rỗng."""
    lit = [Op(a or b, None, None, "unknown") for a, b in PY_WRITE.findall(code)]
    return lit or ([Op("", None, None, "unknown")] if PY_WRITE_ANY.search(code) else [])


def _seg_ops(toks):
    words, outs = _parse(toks)
    if not words:
        return []
    w0, args = words[0], words[1:]
    pos = [w for w in args if not w.startswith("-")]
    if w0 in ("bash", "sh", "zsh"):  # bash -lc 'script': phân tích script
        j = next((j for j, w in enumerate(args) if re.fullmatch(r"-[a-z]*c[a-z]*", w)), None)
        return _shell_ops(args[j + 1]) if j is not None and j + 1 < len(args) else []
    if w0 in ("sed", "perl") and any(w == "--in-place" or w.startswith("--in-place=") or re.fullmatch(r"-[A-Za-z]*i\S*", w) for w in args):
        files, script, it = [], False, iter(args)
        for w in it:
            if w in ("-e", "-f", "--expression", "--file") or (w0 == "perl" and re.fullmatch(r"-[A-Za-z]*e", w)):
                next(it, None)
                script = True
            elif w.startswith(("--expression=", "--file=")):
                script = True
            elif not w.startswith("-"):
                files.append(w)
        return [Op(f, None, None, "unknown") for f in (files if script else files[1:])]  # không có -e: đối số đầu là script
    if w0 in ("echo", "printf"):
        return [Op(outs[-1][1], None, None, "unknown")] if outs else []
    if w0 in ("cp", "mv"):
        return [Op(pos[-1], None, None, "unknown")] if len(pos) >= 2 else []
    if w0 == "patch" or (w0 == "git" and pos[:1] == ["apply"]):
        return [Op("", None, None, "unknown")]
    if w0 == "rm":
        return [Op(w, None, None, "delete") for w in pos]
    code = " ".join(words)
    return _py_ops(code) if PYTHON.fullmatch(w0) else [Op(a or b, None, None, "unknown") for a, b in PY_WRITE.findall(code)]


def _heredoc_ops(segs, body):
    """Dòng mở heredoc: lệnh nhận heredoc (cat/tee ghi file, python, patch/git apply) dùng thân heredoc; lệnh khác như thường."""
    ops, fed = [], None
    for toks in segs:
        if fed is None and "<<" in toks:
            words, outs = _parse(toks)
            w0, fed = (words or [""])[0], []
            if w0 == "cat" and outs:
                fed = [Op(outs[-1][1], None, body, "fragment" if outs[-1][0] in (">>", "&>>") else "whole")]
            elif w0 == "tee" and (files := [w for w in words[1:] if not w.startswith("-")]):
                fed = [Op(files[0], None, body, "fragment" if {"-a", "--append"} & set(words) else "whole")]
            elif PYTHON.fullmatch(w0):
                fed = _py_ops(body)
            elif w0 == "patch" or words[:2] == ["git", "apply"]:
                paths = [p for p in re.findall(r"^\+\+\+ (?:[ab]/)?(\S+)", body, re.M) if p != "/dev/null"]
                fed = [Op(p, None, None, "unknown") for p in paths] or [Op("", None, None, "unknown")]
            ops += fed
        elif fed == [] and toks[:1] == ["tee"]:  # cat <<EOF | tee f
            words, _ = _parse(toks)
            files = [w for w in words[1:] if not w.startswith("-")]
            fed = [Op(files[0], None, body, "fragment" if {"-a", "--append"} & set(words) else "whole")] if files else []
            ops += fed
        else:
            ops += _seg_ops(toks)
    return ops


def _shell_ops(cmd):
    """Thao tác ghi file của một lệnh shell. Dòng logic = các dòng nối nhau khi nháy chưa đóng hoặc dòng kết thúc bằng gạch chéo ngược."""
    lines, ops, i, join = (cmd or "").replace("\r\n", "\n").split("\n"), [], 0, True
    while i < len(lines):
        start, line = i, lines[i]
        i += 1
        if not line.strip():
            continue
        parts, q = [line], _quote_state(line) if join else None
        while q and i < len(lines):
            if q == "\\":
                parts[-1], q = parts[-1][:-1] + " ", _quote_state(lines[i])
                parts.append(lines[i])
            else:
                q = _quote_state(lines[i], q)
                parts.append("\n" + lines[i])
            i += 1
        if q:  # tới hết lệnh vẫn chưa đóng: chỉ xét dòng đầu, các dòng sau xét riêng (giữ thời gian tuyến tính)
            i, join = start + 1, False
        else:
            line = "".join(parts)
        segs = list(_segments(line))
        marker = next((t[j + 1].lstrip("-") for t in segs for j in range(len(t) - 1)
                       if t[j] == "<<" and re.fullmatch(r"-?[A-Za-z_]\w*", t[j + 1])), None)
        if marker is None:
            for toks in segs:
                ops += _seg_ops(toks)
            continue
        body = []
        while i < len(lines) and lines[i].strip() != marker:  # heredoc thiếu dòng kết thúc → lấy tới hết lệnh
            body.append(lines[i])
            i += 1
        i += 1
        ops += _heredoc_ops(segs, "\n".join(body))
    return ops


def edit_ops(step):
    """Các thao tác sửa file của một step (design.md §2.2); phần không biết (kể cả args không phải chuỗi) để None."""
    if step.kind == "edit":
        a = _args(step)
        s = lambda k: v if isinstance(v := a.get(k), str) else None
        cmd, path = a.get("command", ""), s("path") or ""
        if cmd == "create":
            return [Op(path, None, "" if a.get("file_text") is None else s("file_text"), "whole")]
        if cmd == "str_replace":
            return [Op(path, s("old_str"), s("new_str"), "fragment")]
        if cmd in ("insert", "edit"):
            return [Op(path, None, s("new_str"), "fragment")]
        if cmd == "undo_edit":
            return [Op(path, None, None, "undo")]
        return [Op(path, None, None, "unknown")]
    if step.kind == "python":  # ô IPython là code Python; dòng `!lệnh` là shell
        code = step.command or ""
        return _py_ops(code) + [o for l in code.split("\n") if l.lstrip().startswith("!") for o in _shell_ops(l.lstrip()[1:])]
    if step.kind in ("shell", "input"):
        return _shell_ops(step.command)
    return []


def exec_error(text):
    return bool(EXEC_ERR.search(text or ""))


def editor_error(text):
    """Lỗi editor; bỏ các dòng nội dung file có đánh số trước khi tìm (file có thể chứa chính chuỗi "does not exist")."""
    return bool(EDITOR_ERR.search(NUMBERED.sub("", text or "")))


def error_signature(text):
    """Dòng cuối cùng khớp mẫu lỗi; với traceback là dòng exception (dòng không thụt lề đầu tiên sau tiêu đề). Chữ số → 0, ≤ 200 ký tự."""
    lines = (text or "").split("\n")
    hits = [i for i, l in enumerate(lines) if EXEC_ERR.search(l) or EDITOR_ERR.search(l)]
    if not hits:
        return None
    line = lines[hits[-1]]
    if "Traceback (most recent call last)" in line:
        line = next((l for l in lines[hits[-1] + 1:] if l.strip() and not l[0].isspace()), line)
    return re.sub(r"\d+", "0", line.strip())[:200]


def state_head(step):
    """Trạng thái trừu tượng: kind + chương trình chạy (shell/python/input) hoặc basename của path (edit/view). Path theo POSIX."""
    if step.kind in ("shell", "python", "input"):
        m = re.match(r"[^\s;&|()]+", PREFIX.sub("", (step.command or "").strip()))
        return f"{step.kind}:{posixpath.basename(m.group(0)) if m else ''}"
    if step.kind in ("edit", "view"):
        path = _args(step).get("path") or ""
        if not path and len(words := (step.command or "").split()) > 1:  # SWE-agent: open a.py, search_dir foo
            path = words[1]
        return f"{step.kind}:{posixpath.basename(str(path).rstrip('/'))}"
    return f"{step.kind}:"


def action_key(step):
    """Khóa action: kind + lệnh chuẩn hóa khoảng trắng + args (JSON sắp key); không gồm message, trừ think (nội dung think nằm ở message)."""
    parts = [step.kind, " ".join((step.command or "").split()), json.dumps(_args(step), sort_keys=True, ensure_ascii=False, default=str)]
    s = "\x1f".join(parts + [step.message or ""] if step.kind == "think" else parts)
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:12]


def is_test_path(path):
    return bool(TEST_PATH.search((path or "").replace("\\", "/")))


def scan_text(obs, prev_obs="", cmd=""):
    """Phần observation để chạy regex: 10.000 ký tự cuối. Ảnh chụp toàn màn hình (#15): còn echo lệnh của step (dòng prompt
    bắt đầu bằng dòng lệnh đầu, cắt 25 ký tự) → chỉ giữ các dòng sau echo cuối; không thấy → giữ các dòng chưa có ở prev_obs.

    prev_obs = màn hình gần nhất + output mới từ sau nó (Tracker giữ), không chỉ observation ngay trước.
    """
    obs = (obs or "").replace("\r\n", "\n")
    first = next((l.strip() for l in (cmd or "").split("\n") if l.strip()), "")[:25]
    if SCREEN_MARKER in obs and first:  # chạy lại cùng lệnh ra cùng lỗi: phép trừ bên dưới sẽ coi là dòng cũ
        lines = obs.split("\n")
        echo = [i for i, l in enumerate(lines) if first in l and (m := PROMPT.match(l)) and m.group(1).startswith(first)]
        if echo:
            return "\n".join(lines[echo[-1] + 1:])[-TAIL:]
    if SCREEN_MARKER in obs and prev_obs:  # trừ theo số lần xuất hiện: lỗi in lại (trùng dòng cũ) vẫn là dòng mới
        left, keep = Counter(prev_obs.replace("\r\n", "\n").split("\n")), []
        for l in obs.split("\n"):
            if left[l] > 0:
                left[l] -= 1
            else:
                keep.append(l)
        obs = "\n".join(keep)
    return obs[-TAIL:]


def tokens(text):
    return {w for w in (t.lower() for t in IDENT.findall(text or "")) if w not in STOPWORDS}


TEST_CMD = re.compile(  # docs/2026-10-20-p0-probe/scripts/probe.py
    r"\b(pytest|py\.test|unittest|tox|nox|jest|mocha|vitest|rspec|phpunit|ctest)\b|\b(npm|yarn|pnpm)\s+(run\s+)?test\b"
    r"|\bgo\s+test\b|\bcargo\s+test\b|\bmvn\b[^\n]*\btest\b|\bgradlew?\b[^\n]*\btest\b|\bmake\s+(check|test)\b"
    r"|\bpython3?\s+\S*test\S*\.py\b|\bdotnet\s+test\b")
RUN = re.compile(  # docs/2026-10-20-p0-probe/scripts/diag_g3g4.py: lệnh thật sự chạy framework test
    r"(^|[;&|(\"'`]\s*)(timeout\s+\d+\s+)?(\w+=\S*\s+)*((uv|poetry|pipenv)\s+run\s+)?(\S*/)?"
    r"((python3?\s+-m\s+)?(pytest|py\.test|unittest|tox|nox)|(go|cargo|dotnet)\s+test|(npm|yarn|pnpm)\s+(run\s+)?test"
    r"|make\s+(check|test)|jest|mocha|vitest|rspec|phpunit|ctest|mvn|\./gradlew|gradle)\b|python3?(\.\d+)?\s+-m\s+(pytest|unittest)\b", re.M)
INSTALL = re.compile(r"\b(pip3?|apt(-get)?|conda|npm|yarn|pnpm|uv)\s+(install|add|i)\b")
PYTEST_SUM = re.compile(r"^.*\b\d+ (?:passed|failed|errors?|skipped|xfailed|xpassed|deselected|warnings?)\b.*\bin [\d.]+s\b.*$", re.M)


def _strip_heredoc(cmd):
    out, marker = [], None
    for line in (cmd or "").replace("\r\n", "\n").split("\n"):
        if marker:
            marker = None if line.strip() == marker else marker
            continue
        out.append(line)
        m = HEREDOC.search(line)
        marker = m.group(2) if m else None
    return "\n".join(out)


def test_kind(command):
    """"framework" | "adhoc" | None — phân loại như chẩn đoán P0 §4.1 (cài gói, chỉ xem, từ khóa trong heredoc → None)."""
    c = _strip_heredoc(command)
    m = TEST_CMD.search(c)
    if not m or INSTALL.search(c):
        return None
    if m.group(0).startswith("python"):
        return "adhoc"
    return None if re.search(r"--version|--help|--collect-only", c) or not RUN.search(c) else "framework"


def test_summary(text):
    """(số passed, số failed + error) từ dòng tổng kết test cuối cùng; None nếu không có dòng tổng kết."""
    n = lambda pat, s: sum(int(x) for x in re.findall(pat, s))
    if m := re.findall(r"test result: (?:ok|FAILED)\. (\d+) passed; (\d+) failed", text or ""):  # cargo: một dòng mỗi binary → cộng
        return sum(int(p) for p, _ in m), sum(int(f) for _, f in m)
    if hits := PYTEST_SUM.findall(text or ""):
        s = hits[-1]
        return n(r"(\d+) passed", s), n(r"(\d+) failed", s) + n(r"(\d+) errors?\b", s)
    if m := list(re.finditer(r"^Ran (\d+) tests? in", text or "", re.M)):
        f = re.search(r"^(?:OK|FAILED)(?: \(([^)]*)\))?", text[m[-1].end():], re.M)
        kv = {k.strip(): int(v) for k, v in re.findall(r"([a-z ]+)=(\d+)", (f.group(1) or "") if f else "")}
        bad, skip = kv.get("failures", 0) + kv.get("errors", 0), kv.get("skipped", 0) + kv.get("expected failures", 0)
        return max(0, int(m[-1].group(1)) - bad - skip), bad  # max: lỗi setUpModule/import cho errors không nằm trong số Ran
    if m := re.findall(r"Tests:\s+(?:(\d+) failed, )?(?:\d+ skipped, )?(?:(\d+) passed, )?\d+ total", text or ""):
        return int(m[-1][1] or 0), int(m[-1][0] or 0)
    if m := re.findall(r"Tests run: (\d+), Failures: (\d+), Errors: (\d+)", text or ""):
        run, f, e = map(int, m[-1])
        return max(0, run - f - e), f + e
    if re.search(r"^(?:ok|FAIL)\s+\S+\s+(?:[\d.]+s|\(cached\))|^--- (?:PASS|FAIL):", text or "", re.M):  # go test: số test, không có thì số package
        fails = len(re.findall(r"^\s*--- FAIL:", text, re.M)) or len(re.findall(r"^FAIL\s+\S+\s+[\d.]+s", text, re.M))
        passes = len(re.findall(r"^\s*--- PASS:", text, re.M)) or len(re.findall(r"^ok\s+\S+\s+(?:[\d.]+s|\(cached\))", text, re.M))
        return passes, fails
    if re.search(r"\d+ (?:passing|failing)\b", text or ""):  # mocha
        return n(r"(\d+) passing\b", text), n(r"(\d+) failing\b", text)
    if re.search(r"\bno tests ran\b", text or ""):
        return 0, 0
    return None


RUFF, RUFF_VERSION = "ruff", "0.15.12"
# Mã ruff KHÔNG tin được khi chạy trên đoạn code: precision < 0,9 với ≥ 10 lỗi trên cửa sổ (design.md §2.4).
# Ghim từ probe không dùng nhãn results/p2-ruff-fragments/20261021-103733_seed0. Mã còn lại (kể cả mã hiếm) được đếm.
FRAGMENT_UNSAFE = frozenset({"F401", "F701", "F702", "F704", "F706", "F821", "F841", "invalid-syntax"})


def ruff_findings(code):
    """[(mã, dòng)] của ruff E9/F/S; None nếu không có ruff, ruff lỗi hoặc quá 10 s. --isolated: bỏ qua cấu hình của repo."""
    try:
        r = subprocess.run([RUFF, "check", "--isolated", "--no-cache", "--select", "E9,F,S", "--output-format", "json",
                            "--stdin-filename", "x.py", "-"], input=code.encode("utf-8"), capture_output=True, timeout=10)  # bytes: không đổi \n → \r\n
        return [(d.get("code") or "", d["location"]["row"]) for d in json.loads(r.stdout)]
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        return None


def lint_counts(code, fragment):
    """(lỗi cú pháp, F, S). Đoạn code: dedent trước và bỏ mã trong FRAGMENT_UNSAFE. None nếu không chạy được ruff."""
    found = ruff_findings(textwrap.dedent(code) if fragment else code)
    if found is None:
        return None
    codes = [c for c, _ in found if not fragment or c not in FRAGMENT_UNSAFE]
    return (sum(c == "invalid-syntax" or c.startswith("E9") for c in codes), sum(c.startswith("F") for c in codes),
            sum(c.startswith("S") for c in codes))
