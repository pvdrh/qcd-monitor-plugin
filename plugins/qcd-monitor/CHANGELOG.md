# Changelog

Định dạng theo [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); số phiên bản theo [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] — 2026-11-18

### Thay đổi

- Model phát hành ở dạng `m1.json`, không phụ thuộc phiên bản Python, thay cho `m1.bin` (`marshal` của Python 3.10). Lần đầu chạy trên một bản Python, hook ghi bản nạp nhanh vào thư mục `cache/` trong thư mục dữ liệu; các lần sau nạp từ đó.
- Điểm W không đổi: phát lại cùng một chuỗi 108 step (phiên thật của spike cộng 40 lô tổng hợp) cho kết quả trùng từng bit giữa bản 0.1.0 trên Python 3.10 và bản 0.2.0 trên Python 3.10, 3.12, 3.13.

### Thêm

- Thông báo cho người dùng, mỗi sự cố một lần trong phiên và ở mọi chế độ, khi không nạp được model hoặc không dùng được ruff 0.15.12. `/qcd-status` ghi thêm các sự cố đã gặp trong phiên.
- README: hướng dẫn cho macOS và Linux khi máy chỉ có lệnh `python3`.
- `plugin.json`: trường `author`.

## [0.1.0] — 2026-10-27

- Bản đầu: hook `UserPromptSubmit` và `PostToolBatch`, ba chế độ `observe`/`warn`/`strict`, lệnh `/qcd-status`, model M1-6 (seed 0), ngưỡng hiệu chỉnh trên Terminal-Bench 2 cho α = 0.1 và 0.05.
