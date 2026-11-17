# qcd-monitor

**qcd-monitor** là plugin cho Claude Code. Plugin theo dõi các tool call của agent trong từng lượt prompt và báo động khi chuỗi tool call có dấu hiệu giống các run thất bại. Mọi tính toán chạy cục bộ: plugin không gọi LLM và không gửi dữ liệu ra ngoài.

> [!WARNING]
> **Bản nghiên cứu 0.1.0.** Báo động là một tín hiệu thống kê về kết cục của cả run, không phải kết luận rằng code sai. Tỷ lệ báo động giả mới được kiểm trên một tổ hợp dữ liệu duy nhất. Hãy đọc mục [Giới hạn](#giới-hạn) trước khi dựa vào plugin.

## Tính năng chính

- **Chạy cục bộ:** hook chỉ dùng thư viện chuẩn của Python; công cụ ngoài duy nhất là `ruff`.
- **Ba chế độ phản ứng:** `observe` (chỉ ghi log), `warn` (cảnh báo), `strict` (dừng agent).
- **Ngưỡng có hiệu chỉnh:** chọn mức báo động giả danh nghĩa α là `0.1` hoặc `0.05`.
- **Fail-open:** lỗi bên trong hook không chặn agent.
- **Lệnh `/qcd-status`:** xem trạng thái của run hiện tại.

## Cách hoạt động

- **Run:** mỗi prompt bạn gửi mở một run mới. Run gồm mọi tool call của agent cho tới prompt tiếp theo.
- **Sau mỗi lô tool call** (Bash, Edit, Write, Read…), hook thực hiện ba bước:
  1. Tính 49 đặc trưng từ tool call và kết quả trả về, ví dụ lệnh có báo lỗi không, code Python vừa ghi có lỗi `ruff` không.
  2. Chấm điểm từng tool call bằng một model cây quyết định gọn (LightGBM, đóng gói sẵn).
  3. Cộng dồn điểm theo CUSUM thành giá trị **W** của run.
- **Báo động** khi W vượt ngưỡng **h**. Mỗi run báo động tối đa một lần.
- **Phản ứng** khi báo động phụ thuộc chế độ đang chọn (xem [Chế độ](#chế-độ)).

## Yêu cầu

| Thành phần | Yêu cầu |
|---|---|
| Claude Code | ≥ 2.1.283 |
| Python | Lệnh `python` trên PATH. Đã kiểm với Python 3.10.0; các bản khác chưa kiểm (xem [Giới hạn](#giới-hạn)). |
| ruff | Đúng bản 0.15.12, trên PATH. Nếu thiếu, plugin vẫn chạy nhưng 5 đặc trưng lint bị bỏ trống và ngưỡng không còn khớp với lúc hiệu chỉnh. |
| Hệ điều hành | Mới kiểm trên Windows 10. |

Kiểm tra môi trường:

```powershell
python --version          # phải là Python thật, không mở Microsoft Store
pip install ruff==0.15.12
ruff --version            # phải in ra: ruff 0.15.12
```

Trên Windows, nếu `python --version` mở Microsoft Store, hãy tắt lối tắt tại *Settings → Apps → Advanced app settings → App execution aliases*.

## Cài đặt

Trong Claude Code:

```
/plugin marketplace add pvdrh/qcd-monitor-plugin
/plugin install qcd-monitor@qcd-monitor
```

- **Quyền truy cập:** repo đang ở chế độ private, nên tài khoản GitHub của bạn cần quyền đọc repo.
  - Nếu SSH key của bạn đăng nhập được github.com, Claude Code tự clone qua SSH. Key phải dùng được mà không hỏi passphrase, và `github.com` phải có sẵn trong `known_hosts`.
  - Nếu không, Claude Code clone qua HTTPS bằng git credential helper của bạn.
- **Tùy chọn khi cài:** Claude Code hỏi hai tùy chọn. Khuyến nghị lúc đầu chọn `mode` = `observe` và `alpha` = `0.1`.
- **Kiểm tra:** gửi một prompt có tool call, rồi gõ `/qcd-status`.

## Sử dụng

### Chế độ

| Chế độ | Khi báo động |
|---|---|
| `observe` (mặc định) | Không hiện gì, chỉ ghi log. Xem trạng thái bằng `/qcd-status`. |
| `warn` | Hiện cảnh báo `qcd-monitor: báo động ở step <k> của run (W = … > h = …, α = …)`, đồng thời thêm câu này vào ngữ cảnh của agent. |
| `strict` | Dừng agent ngay, kèm lý do là câu cảnh báo trên. |

### Đổi chế độ hoặc α

Dùng một trong ba cách:

- gõ `/plugin configure qcd-monitor`;
- vào `/plugin`, tab **Installed**, chọn **qcd-monitor**, rồi **Configure options**;
- vào `/config`: mỗi tùy chọn của plugin là một dòng.

Sau khi đổi, chạy `/reload-plugins` hoặc mở phiên mới.

> [!IMPORTANT]
> Không gỡ rồi cài lại chỉ để đổi chế độ: gỡ plugin sẽ xóa thư mục dữ liệu (xem [Cập nhật và gỡ cài đặt](#cập-nhật-và-gỡ-cài-đặt)).

### Xem trạng thái

Gõ `/qcd-status` (hoặc `/qcd-monitor:qcd-status`). Lệnh này không mở run mới. Ví dụ kết quả (số minh họa):

```
qcd-monitor status: run 2, 14 step, W = 3.218, h = 15.664 (α = 0.1), báo động: chưa, chế độ observe, model M1-6 acae504962b4
```

| Trường | Ý nghĩa |
|---|---|
| `run 2` | Run thứ 2 trong phiên hiện tại. |
| `14 step` | Số tool call đã được chấm điểm trong run. |
| `W` | Điểm cộng dồn hiện tại. |
| `h` | Ngưỡng báo động ứng với α đang chọn. |
| `báo động` | `chưa`, hoặc step tại đó đã báo động. |
| `model` | Tên model và 12 ký tự đầu của sha256 (xem [Dữ liệu train và điều khoản](#dữ-liệu-train-và-điều-khoản)). |

### Thử nhanh cả ba chế độ

Biến môi trường `QCD_TEST_FORCE_ALARM=1` buộc hook báo động ngay ở lô tool call đầu tiên của mỗi run. Biến này chỉ dùng để thử; các dòng log khi thử được đánh dấu `force_alarm`.

```powershell
mkdir qcd-try; cd qcd-try
$env:QCD_TEST_FORCE_ALARM = "1"
claude
```

Gửi một prompt có tool call, ví dụ "tạo file hello.py in ra hello rồi chạy nó", rồi gõ `/qcd-status`. Đổi chế độ (xem mục trên) và lặp lại. Thử xong:

- đóng cửa sổ terminal để bỏ biến môi trường;
- đặt lại chế độ `observe`, vì chế độ là cấu hình chung cho mọi phiên Claude Code.

## Chọn α

α là mức báo động giả danh nghĩa tính theo run. Ngưỡng h được hiệu chỉnh trên dữ liệu Terminal-Bench 2 sao cho tỷ lệ run **thành công** bị báo động không vượt quá α.

| Tùy chọn `alpha` | Ngưỡng h | Ghi chú |
|---|---|---|
| `0.1` | `15.664` | Khuyến nghị lúc đầu. |
| `0.05` | `35.195` | Ngưỡng cao hơn nên ít báo động hơn, cả báo động đúng lẫn báo động nhầm. |

Đảm bảo này chỉ đúng trên dữ liệu hiệu chỉnh, không chắc đúng với công việc của bạn (xem [Giới hạn](#giới-hạn)).

## Kết quả đánh giá

- **Dữ liệu:** Terminal-Bench 2, gồm 147 run thành công và 286 run thất bại của Claude Code dùng model GLM-4.7. Ngưỡng được hiệu chỉnh qua 5 lượt chia task. Mỗi số là trung bình trên 5 seed của model; trong ngoặc vuông là khoảng tin cậy 95% (bootstrap theo task).
- **Đối chứng B0:** quy tắc đơn giản, báo động khi số step của run vượt một ngưỡng được hiệu chỉnh theo cùng cách.

| Chỉ số | α | qcd-monitor (M1-6) | B0 |
|---|---|---|---|
| Run thành công bị báo động (báo động giả) | `0.1` | 6,1% [1,9; 11,3] | 6,9% [2,4; 12,9] |
| | `0.05` | 3,0% [0,6; 6,2] | 2,7% [0,6; 5,6] |
| Run thất bại bị báo động | `0.1` | 27,3% [19,5; 35,6] | 26,9% [18,7; 35,7] |
| | `0.05` | 15,9% [9,5; 23,2] | 12,4% [6,4; 19,3] |

- **Khả năng phát hiện run thất bại:** ở α = `0.1`, kiểm định hoán vị cho p = 0,90. Dữ liệu không phân biệt được qcd-monitor với B0; hiện chưa có bằng chứng model phát hiện run thất bại tốt hơn quy tắc đếm step.
- **Bản model trong plugin (seed 0):** tỷ lệ run thành công bị báo động là 7,5% ở α = `0.1`. Nếu tính trung bình theo task, tỷ lệ này là 12,0%, vượt α.
- **Ngưỡng đóng gói** được hiệu chỉnh trên toàn bộ dữ liệu, nên không có ước lượng báo động giả trên dữ liệu chưa thấy cho chính ngưỡng này.
- **Độ trễ của hook** (Windows 10, CPU Intel 8 luồng, phát lại 20.421 lần gọi hook):
  - trung vị khoảng 137 ms mỗi tool call; p95 là 176 ms, dưới ngân sách 200 ms;
  - riêng các lô có `Write`, p95 khoảng 190 ms, sát ngân sách;
  - B0 có p95 là 138 ms.

## Dữ liệu lưu trên máy

Thư mục dữ liệu: `~/.claude/plugins/data/qcd-monitor-qcd-monitor/` (trên Windows: `%USERPROFILE%\.claude\plugins\data\qcd-monitor-qcd-monitor\`).

- **`log/<ngày>.jsonl`:** mỗi lần gọi hook ghi một dòng, gồm:
  - thời điểm, sự kiện, `session_id`, số run, số step;
  - dãy W, trạng thái báo động, chế độ, α;
  - sha256 của model và thời gian từng phần;
  - tên lớp lỗi, nếu có.

  Log không ghi nội dung code, lệnh hay prompt.
- **`state/<16 ký tự đầu của sha256(session_id)>.json`:** mỗi phiên một file, giữ những gì cần để tính đặc trưng giữa các lô:
  - prompt của run hiện tại;
  - output tool gần nhất, tối đa 20.000 ký tự;
  - các dòng lỗi, đường dẫn file, tập token của code và prompt.
- File state không được cập nhật quá 7 ngày sẽ tự bị xóa khi có prompt mới. Bạn có thể xóa cả thư mục dữ liệu bất cứ lúc nào.

> [!CAUTION]
> File state **chứa văn bản thật**. Output tool có thể chứa mật khẩu hoặc token nếu chúng từng xuất hiện trong output.

## Xử lý sự cố

| Triệu chứng | Nguyên nhân và cách xử lý |
|---|---|
| Hook gặp lỗi bên trong | Hook bỏ qua lỗi (fail-open): thoát bình thường, không in gì, không chặn agent, chỉ ghi tên lớp lỗi vào trường `error` của log. |
| `/qcd-status` không hiện dòng trạng thái | Kiểm `python --version`; kiểm plugin đang bật trong `/plugin`; xem dòng cuối của file log. |
| Claude Code chặn mọi prompt sau khi thư mục cache plugin bị xóa giữa phiên | Script hook không còn, nên Python thoát với mã 2 và Claude Code chặn prompt. Tắt plugin (`/plugin`, tab **Installed**, chọn qcd-monitor rồi tắt), hoặc cập nhật plugin (**Update now**). |
| ruff chạy chậm | Hook không có hạn chót bên trong. Lô nào vượt timeout 30 giây của hook sẽ bị Claude Code ngắt, và lô đó không được tính vào run. |

## Cập nhật và gỡ cài đặt

- **Cập nhật:**
  ```
  /plugin marketplace update qcd-monitor
  ```
  Sau đó vào `/plugin`, tab **Installed**, chọn qcd-monitor, **Update now**. Bản mới có hiệu lực từ phiên sau, hoặc sau khi chạy `/reload-plugins`.
- **Tắt tạm thời:** vào `/plugin`, tab **Installed**, chọn qcd-monitor rồi tắt; hoặc chạy trong terminal:
  ```
  claude plugin disable qcd-monitor@qcd-monitor
  ```
- **Gỡ cài đặt:**
  ```
  claude plugin uninstall qcd-monitor@qcd-monitor
  ```
  Lệnh này **xóa luôn thư mục dữ liệu** (log và state). Muốn giữ log, thêm `--keep-data`.

## Giới hạn

- **Tỷ lệ báo động giả chỉ được kiểm trên một tổ hợp:** Claude Code, model GLM-4.7, task Terminal-Bench 2. Khi dùng Claude Code với model Claude trên task của bạn, không có đảm bảo nào.
  - Mỗi run của Terminal-Bench 2 là một prompt dài; khi dùng thật thường có nhiều run ngắn giữa các prompt.
  - Trên dữ liệu đánh giá, điểm mỗi step có xu hướng tăng theo vị trí của step trong run, nên run ngắn và run dài cho kết quả khác nhau.
- **Không kiểm được báo động giả khi dùng thật,** vì log không có nhãn run nào thành công, run nào thất bại.
- **α tính theo từng run.** Ngày có nhiều run sẽ có nhiều báo động giả. Ví dụ minh họa: với tỷ lệ 10%, 20 run thành công trong một ngày cho trung bình 2 báo động giả.
- **Báo động nói về kết cục của cả run,** không chỉ ra step nào bắt đầu sai.
- **Dữ liệu train bị thu hẹp:** model được train trên CodeTraceBench sau khi bỏ các task trùng với Terminal-Bench 2. Việc bỏ này làm mất khoảng 40% số run thất bại có đánh dấu thời điểm hỏng (còn 604 trên 1.012).
- **Tên task gần giống nhau:** vài cặp task giữa dữ liệu train và Terminal-Bench 2 có tên gần nhau, ví dụ `install-windows-xp` và `install-windows-3.11`. Nội dung của chúng có trùng nhau không thì chưa kiểm.
- **Quy tắc chuyển đổi không có trong dữ liệu train:** cách chuyển tool call của Claude Code sang dạng model hiểu là quy tắc mới. Ví dụ, Edit hoặc Read bị chặn được coi là step lỗi (thêm tiền tố `ERROR:`), và output của Read được chuẩn hóa.
- **Thiếu ruff 0.15.12** thì không còn đảm bảo về tỷ lệ báo động giả.
- **Phạm vi theo dõi:** bỏ qua tool call bên trong subagent; chỉ dùng tool call, không dùng lời của agent; mới thử trên Windows.
- **Phiên bản Python:** plugin mới chạy thử với Python 3.10.0 trên Windows 10; các bản cũ hơn hay mới hơn chưa được kiểm.
  - Model được lưu bằng `marshal` của Python 3.10. Theo [tài liệu Python](https://docs.python.org/3/library/marshal.html), định dạng `marshal` có thể thay đổi giữa các phiên bản.
  - Nếu Python của bạn không đọc được file model, hook bỏ qua lỗi (xem [Xử lý sự cố](#xử-lý-sự-cố)). Khi đó plugin không chấm điểm, không báo động và không hiện gì: trông như vẫn chạy nhưng thực ra không theo dõi.
  - Cách nhận ra: sau một prompt có tool call, `/qcd-status` vẫn báo `0 step`, và trường `error` của dòng log `PostToolBatch` không rỗng.

## Dữ liệu train và điều khoản

- **Model (M1-6, seed 0):**
  - LightGBM trên 49 đặc trưng;
  - train trên CodeTraceBench (Hugging Face `NJU-LINK/CodeTraceBench`, revision `aa213b8`), gồm quỹ đạo của 6 model nguồn, đã bỏ các task trùng với Terminal-Bench 2;
  - dataset card ghi giấy phép MIT, nhưng repo dataset không có file LICENSE;
  - sha256 của model: `acae504962b4352e6e59f4ee54cccaf57748c27cb787aa5bbb5d07c7046434bb`.
- **Ngưỡng và đánh giá:** Hugging Face `harborframework/terminal-bench-2-leaderboard` (Apache-2.0), revision `572b261`, bài nộp `ClaudeCode__GLM-4.7`.
- **Điều khoản:** điều khoản sử dụng output của các model nguồn **chưa được kiểm xong**. Vì vậy bản này chỉ dành cho dùng thử nội bộ, và repo đang để private. Mục này sẽ được cập nhật trước khi phát hành công khai.
