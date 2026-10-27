# qcd-monitor

Plugin Claude Code giám sát online run của coding agent bằng CUSUM trên 49 feature (mô hình gọn M1). Không gọi LLM, không gửi dữ liệu ra khỏi máy.

## Yêu cầu

- Python >= 3.10 trên PATH, là bản thật, không phải lệnh giả của Microsoft Store.
- ruff 0.15.12. Không có ruff thì 5 feature lint để trống và không có đảm bảo PFA.
- Claude Code >= 2.1.283 (bản đã kiểm trong spike của P5; `options` của userConfig cần >= 2.1.271).
- Hook chạy `python -I -S`: chỉ dùng thư viện chuẩn và gói `qcd` đi kèm, không cần site-packages.

## Cài

```
/plugin marketplace add <repo>
/plugin install qcd-monitor@qcd-monitor
```

Khi cài, chọn `mode` và `alpha`.

## Chế độ

- `observe` (mặc định): chỉ ghi log, không in gì (trừ khi gõ `/qcd-status`).
- `warn`: khi báo động, hiện cảnh báo (`systemMessage`) và thêm vào ngữ cảnh; chỉ báo một lần mỗi run.
- `strict`: khi báo động, dừng agent (`continue: false`); chỉ báo một lần mỗi run.

`UserPromptSubmit` mở run mới. Gõ `/qcd-status` (hoặc `/qcd-monitor:qcd-status`) để xem số step, W so với ngưỡng h và trạng thái báo động của run hiện tại (lệnh này không mở run mới).

## Dữ liệu cục bộ

Mọi thứ nằm ở `${CLAUDE_PLUGIN_DATA}` trên máy bạn. Không có gì được gửi đi đâu.

- `log/<ngày>.jsonl`: mỗi lô tool một dòng, chỉ gồm thời điểm, sự kiện, session_id, run, số step, dãy W, trạng thái báo động, chế độ, alpha, sha model, thời gian từng phần và tên lớp lỗi (nếu có). Log không ghi nội dung code, lệnh hay prompt.
- `state/<16 ký tự đầu của sha256(session_id)>.json`: mỗi phiên một file, chứa toàn bộ Tracker để tính feature giữa các lô. Trong đó có:
  - prompt của run hiện tại;
  - output tool gần nhất (tối đa 20.000 ký tự, có thể chứa bí mật);
  - các dòng lỗi;
  - đường dẫn file;
  - các tập token của code và prompt.
- State không được cập nhật quá 7 ngày sẽ tự bị xóa (khi có prompt mới).
- Bạn có thể xóa thư mục `${CLAUDE_PLUGIN_DATA}` bất cứ lúc nào.

## Giới hạn

- PFA của RQ4 chỉ đúng cho quần thể "Claude Code + GLM-4.7 + task TB2".
  - Không có đảm bảo cho phiên dùng thật: khác model (Claude), khác loại task, và khác phân bố độ dài run. Trial TB2 là một run dài một prompt; khi dùng thật là nhiều run ngắn giữa các prompt (X1, X3 cho thấy ℓ tăng theo k).
  - Log phiên thật không có nhãn, nên không kiểm được PFA.
- Với α = 0,1 theo run, một ngày có nhiều run sẽ có báo động giả. Bài báo PFA theo run để người dùng tự hình dung.
- Tỷ lệ báo động trên F đo theo kết cục cả run, không đo theo τ.
- M1-6 mất khoảng 40% run F⁺ so với bài chính; M1-DS còn rất ít dữ liệu. Model đóng gói khác các model của RQ1–RQ3.
- Có vài cặp tên task gần nhau giữa phần train và TB2, ví dụ `install-windows-xp`/`install-windows-3.11`. Nội dung có trùng không thì chưa kiểm.
- Ánh xạ lỗi của edit/view (`ERROR:`) và chuẩn hóa Read là quy tắc mới, không có trong dữ liệu train.
- Không có ruff (hoặc khác phiên bản) thì không có đảm bảo PFA.
- Bỏ tool call trong subagent; không có lời agent (S1); chỉ thử trên Windows.
- Hook không có hạn chót bên trong (khác S2, #28): khi ruff chậm, hook có thể vượt timeout 30 s; lô đó không vào state.
- Script hook bị mất giữa phiên (cache plugin bị xóa) làm Python thoát mã 2, Claude Code chặn prompt cho tới khi tắt plugin (#28).
- Báo động không có nghĩa là phát hiện lỗi. Số liệu ở báo cáo P5.

## Nguồn dữ liệu train và điều khoản

Sẽ được hoàn thành sau khi kiểm tra điều khoản (Task 15).
