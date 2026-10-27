"""Lõi detector dùng chung (design.md §3): CUSUM, ngưỡng conformal có phá hòa ngẫu nhiên, báo động, kết cục, τ̂, vai trò fold."""
import hashlib
import math
import random
from collections import defaultdict
from itertools import accumulate

INF = float("inf")


def cusum(incs, delta=0.0):
    """W_k = max(0, W_{k−1} + ℓ_k − δ), W_0 = 0. NaN (gia số NaN hoặc inf − inf) → ValueError: max(0, NaN) âm thầm đặt lại về 0."""
    def step(w, x):
        if math.isnan(v := w + x - delta):
            raise ValueError("CUSUM gặp NaN")
        return max(0.0, v)
    return list(accumulate(incs, step, initial=0.0))[1:]


def _no_nan(stats):
    if any(math.isnan(s) for s in stats):  # max() và phép so tuple bỏ qua NaN không báo gì
        raise ValueError("thống kê s_k là NaN")
    return stats


def tie_u(seed, traj_id):
    """U ∈ [0, 1) cố định theo (seed, run), dùng để phá thế hòa."""
    return (int.from_bytes(hashlib.sha256(f"{seed}:{traj_id}".encode()).digest()[:8], "big") >> 11) / 2**53


def one_run_per_task(runs, seed):
    """(traj_id, task) → mỗi task rút 1 run theo seed (tập hiệu chỉnh chính)."""
    by = defaultdict(list)
    for t, task in runs:
        by[task].append(t)
    return [random.Random(f"{seed}:{task}").choice(sorted(ids)) for task, ids in sorted(by.items())]


def _kth(pairs, r):
    if any(math.isnan(m) for m, _ in pairs):  # NaN làm thứ tự sắp xếp phụ thuộc thứ tự đầu vào
        raise ValueError("max thống kê M_i là NaN")
    return tuple(sorted(pairs)[r - 1]) if 1 <= r <= len(pairs) else (INF, 1.0)


def conformal_threshold(pairs, alpha):
    """(h, u_h) = cặp (M_i, U_i) nhỏ thứ r = ⌈(n+1)(1−α)⌉; (∞, 1) khi r > n."""
    return _kth(pairs, math.ceil(round((len(pairs) + 1) * (1 - alpha), 9)))


def insample_threshold(pairs, q=0.9):
    """Ngưỡng trong mẫu cho tune: cặp nhỏ thứ ⌈q·n⌉ (không có đảm bảo)."""
    return _kth(pairs, math.ceil(round(len(pairs) * q, 9)))


def first_alarm(stats, u, thr):
    """Bước đầu tiên (đánh số từ 1) có (s_k, U) > (h, u_h) theo thứ tự từ điển; None nếu không báo động."""
    thr = tuple(thr)  # ngưỡng đọc lại từ JSON là list
    return next((k for k, s in enumerate(_no_nan(stats), 1) if (s, u) > thr), None)


def outcome(alarm, tau):
    return "missed" if alarm is None else "premature" if alarm < tau else "detected"


def tau_hat(stats, alarm):
    """τ̂ = 1 + max{j < a : s_j = 0}, s_0 = 0. Chỉ dùng cho detector dạng CUSUM."""
    return 1 + max(j for j in range(alarm) if j == 0 or stats[j - 1] == 0)


def outer_roles(k):
    """Fold test k → fold hiệu chỉnh (k+1) mod 5, train trên 3 fold còn lại (Phần A §6)."""
    cal = (k + 1) % 5
    return {"test": k, "cal": cal, "train": sorted(set(range(5)) - {k, cal})}


def tune_objective(runs):
    """Mục tiêu tune trên MỘT fold validation (Phần B §4); caller lấy trung bình qua 3 inner fold.

    Ngưỡng mỗi scaffold = insample_threshold trên mọi run S của scaffold đó; trả tỷ lệ phát hiện chặt (a ≥ τ) trên F⁺.
    Hàm đọc nhãn pop/tau: theo quyết định #13 CHỈ được gọi trên dữ liệu thật SAU KHI Phần B đã commit (kể cả inner CV).
    """
    pairs = defaultdict(list)
    for r in runs:
        if r["pop"] == "S":
            pairs[r["agent"]].append((max(_no_nan(r["stats"])), r["u"]))
    thr = {a: insample_threshold(p) for a, p in pairs.items()}
    f = [r for r in runs if r["pop"] == "F+"]
    return sum(outcome(first_alarm(r["stats"], r["u"], thr.get(r["agent"], (INF, 1.0))), r["tau"]) == "detected" for r in f) / len(f)
