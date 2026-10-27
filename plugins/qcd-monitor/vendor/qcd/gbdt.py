"""Suy luận GBDT bằng Python thuần từ JSON của LightGBM (`Booster.dump_model()`), không cần cài lightgbm.

Plugin (P5) dùng module này; P3 dùng để kiểm parity (design P3 D9). Nhánh số theo NumericalDecision của LightGBM 4.6.0:
- giá trị thiếu (None/NaN) ở nhánh có missing_type khác "NaN" được coi là 0;
- missing_type "Zero" bắt |x| ≤ 1e-35, "NaN" bắt NaN; giá trị bị bắt đi theo default_left;
- còn lại x ≤ threshold → nhánh trái.
Margin = tổng giá trị lá, cộng tuần tự theo thứ tự cây như LightGBM (math.fsum lệch khoảng 1e-14).
"""
import json
import math

ZERO = 1e-35  # kZeroThreshold của LightGBM


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _leaf(node, x):
    while "leaf_value" not in node:
        if node["decision_type"] != "<=":
            raise ValueError(f"nhánh {node['decision_type']} không được hỗ trợ (chỉ feature số)")
        v = x[node["split_feature"]]
        v = math.nan if v is None else float(v)
        mt = node["missing_type"]
        if math.isnan(v) and mt != "NaN":
            v = 0.0
        if (mt == "Zero" and abs(v) <= ZERO) or (mt == "NaN" and math.isnan(v)):
            left = node["default_left"]
        else:
            left = v <= node["threshold"]
        node = node["left_child"] if left else node["right_child"]
    return node["leaf_value"]


def margin(model, x):
    """x: giá trị feature theo thứ tự model["feature_names"] (None/NaN = thiếu) → logit p."""
    return sum(_leaf(t["tree_structure"], x) for t in model["tree_info"])


def prob(model, x):
    m = margin(model, x)
    return 1 / (1 + math.exp(-m)) if m >= 0 else math.exp(m) / (1 + math.exp(m))  # không tràn số khi |m| lớn


MISSING = {"None": 0, "Zero": 1, "NaN": 2}


def compact(model):
    """Model JSON → tuple cây dạng mảng (nạp nhanh bằng marshal); mỗi cây (gốc, feat, thr, miss, dleft, trái, phải, lá); con âm = lá ~i."""
    out = []
    for t in model["tree_info"]:
        F, TH, MS, DL, L, R, LV = [], [], [], [], [], [], []

        def walk(n):
            if "leaf_value" in n:
                LV.append(float(n["leaf_value"]))
                return ~(len(LV) - 1)
            if n["decision_type"] != "<=":
                raise ValueError(f"nhánh {n['decision_type']} không được hỗ trợ (chỉ feature số)")
            i = len(F)
            F.append(n["split_feature"]), TH.append(float(n["threshold"])), MS.append(MISSING[n["missing_type"]])
            DL.append(bool(n["default_left"])), L.append(0), R.append(0)
            L[i], R[i] = walk(n["left_child"]), walk(n["right_child"])
            return i

        root = walk(t["tree_structure"])
        out.append((root, tuple(F), tuple(TH), tuple(MS), tuple(DL), tuple(L), tuple(R), tuple(LV)))
    return tuple(out)


def margin_c(trees, x):
    """Như margin() trên model đã compact (cùng quy tắc nhánh, cộng tuần tự theo thứ tự cây)."""
    xs, s = [math.nan if v is None else float(v) for v in x], 0.0
    for i, F, TH, MS, DL, L, R, LV in trees:
        while i >= 0:
            v, mt = xs[F[i]], MS[i]
            if math.isnan(v) and mt != 2:
                v = 0.0
            left = DL[i] if (mt == 1 and abs(v) <= ZERO) or (mt == 2 and math.isnan(v)) else v <= TH[i]
            i = L[i] if left else R[i]
        s += LV[~i]
    return s
