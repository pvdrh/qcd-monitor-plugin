# qcd-monitor

Plugin cho Claude Code. Plugin theo dõi các tool call mà agent thực hiện sau mỗi prompt của bạn, và báo động khi chuỗi tool call đó giống các run thất bại. Mọi tính toán chạy trên máy bạn: không gọi LLM, không gửi dữ liệu đi đâu.

> **Bản nghiên cứu 0.1.0.** Plugin đi kèm một bài báo đang viết về phát hiện thay đổi nhanh (quickest change detection) cho coding agent. Báo động chỉ là một tín hiệu thống kê, không có nghĩa là code sai. Đọc mục [Giới hạn](#giới-hạn) trước khi dựa vào nó.

## Cách hoạt động

- **Run:** mỗi lần bạn gửi prompt, plugin mở một run mới. Run gồm mọi tool call của agent cho tới prompt tiếp theo.
- **Sau mỗi lô tool call** (Bash, Edit, Write, Read…), hook làm ba việc:
  1. tính 49 đặc trưng từ tool call và kết quả của nó, ví dụ lệnh có báo lỗi không, code Python vừa viết có lỗi `ruff` không;
  2. chấm điểm từng tool call bằng một model cây quyết định nhỏ (LightGBM, đã đóng gói sẵn);
  3. cộng dồn điểm theo CUSUM thành giá trị **W** của run.
- **Báo động** khi W vượt ngưỡng **h**. Mỗi run báo động tối đa một lần.
- **Phản ứng** khi báo động tùy theo chế độ bạn chọn (mục [Chế độ](#chế-độ)).

## Yêu cầu

| Thành phần | Yêu cầu |
|---|---|
| Claude Code | ≥ 2.1.283 |
| Python | Lệnh `python` trên PATH. Đã kiểm với Python 3.10; bản khác chưa kiểm (mục [Giới hạn](#giới-hạn)). Hook chỉ dùng thư viện chuẩn, không cần cài gói nào. |
| ruff | Đúng bản 0.15.12, trên PATH: `pip install ruff==0.15.12`. Thiếu ruff thì plugin vẫn chạy, nhưng 5 đặc trưng lint để trống và ngưỡng không còn đúng như khi hiệu chỉnh. |
| Hệ điều hành | Mới thử trên Windows 10. |

Trên Windows, `python` phải là Python thật, không phải lối tắt mở Microsoft Store. Kiểm bằng `python --version`. Nếu lệnh này mở Store, tắt lối tắt ở *Settings → Apps → Advanced app settings → App execution aliases*.

Kiểm ruff bằng `ruff --version`; kết quả phải là `ruff 0.15.12`.

## Cài đặt

Trong Claude Code:

```
/plugin marketplace add pvdrh/qcd-monitor-plugin
/plugin install qcd-monitor@qcd-monitor
```

- Repo đang ở chế độ private, nên bạn cần quyền đọc repo:
  - Nếu SSH key của bạn đăng nhập được github.com, Claude Code tự clone qua SSH. Key phải dùng được mà không hỏi passphrase, và `github.com` phải có sẵn trong `known_hosts`.
  - Nếu không, Claude Code clone qua HTTPS bằng git credential helper của bạn.
- Khi cài, Claude Code hỏi hai tùy chọn:
  - `mode`: nên chọn **observe** lúc đầu;
  - `alpha`: nên chọn **0.1**.
- Kiểm tra: gửi một prompt có tool call, rồi gõ `/qcd-status`.

## Chế độ

| Chế độ | Khi báo động |
|---|---|
| `observe` (mặc định) | Không hiện gì, chỉ ghi log. Xem trạng thái bằng `/qcd-status`. |
| `warn` | Hiện cảnh báo `qcd-monitor: báo động ở step <k> của run (W = … > h = …, α = …)`, đồng thời thêm câu đó vào ngữ cảnh của agent. |
| `strict` | Dừng agent ngay, lý do là câu cảnh báo trên. |

## Đổi chế độ hoặc α

Dùng một trong ba cách:
- gõ `/plugin configure qcd-monitor`;
- vào `/plugin`, tab **Installed**, chọn **qcd-monitor**, rồi **Configure options**;
- vào `/config`: mỗi tùy chọn của plugin là một dòng.

Sau khi đổi, chạy `/reload-plugins` hoặc mở phiên mới.

Đừng gỡ rồi cài lại chỉ để đổi chế độ, vì gỡ plugin sẽ xóa thư mục dữ liệu (mục [Gỡ cài đặt](#cập-nhật-và-gỡ-cài-đặt)).

## Xem trạng thái: `/qcd-status`

Gõ `/qcd-status` (hoặc `/qcd-monitor:qcd-status`). Lệnh này không mở run mới. Ví dụ kết quả (số minh họa):

```
qcd-monitor status: run 2, 14 step, W = 3.218, h = 15.664 (α = 0.1), báo động: chưa, chế độ observe, model M1-6 acae504962b4
```

| Trường | Nghĩa |
|---|---|
| `run 2` | run thứ 2 trong phiên này |
| `14 step` | số tool call đã chấm điểm trong run |
| `W` | điểm cộng dồn hiện tại |
| `h` | ngưỡng báo động ứng với α đang chọn |
| `báo động` | `chưa`, hoặc step đã báo động |
| `model` | tên model và 12 ký tự đầu của sha256 |

## Chọn α

- α là mức báo động giả danh nghĩa theo run. Ngưỡng h được hiệu chỉnh trên dữ liệu Terminal-Bench 2 để tỷ lệ run **thành công** bị báo động không vượt quá α.
- Ngưỡng của bản này:
  - α = 0.1: h = 15.664;
  - α = 0.05: h = 35.195 (ngưỡng cao hơn, nên ít báo động hơn, cả báo động đúng lẫn báo động nhầm).
- Đảm bảo này chỉ đúng với dữ liệu hiệu chỉnh, không chắc đúng với công việc của bạn (mục [Giới hạn](#giới-hạn)).

## Thử nhanh cả ba chế độ

Biến môi trường `QCD_TEST_FORCE_ALARM=1` làm hook báo động ngay ở lô tool đầu tiên của mỗi run. Biến này chỉ dùng để thử; log đánh dấu các dòng thử bằng `force_alarm`.

```powershell
mkdir D:\qcd-try; cd D:\qcd-try
$env:QCD_TEST_FORCE_ALARM = "1"
claude
```

Gõ một prompt có tool call, ví dụ "tạo file hello.py in ra hello rồi chạy nó", rồi gõ `/qcd-status`. Đổi chế độ (mục trên) và lặp lại. Thử xong:
- đóng cửa sổ terminal để bỏ biến;
- đặt lại chế độ `observe`, vì chế độ là cấu hình chung cho mọi phiên Claude Code.

## Kết quả đánh giá

**Dữ liệu:** Terminal-Bench 2, 147 run thành công và 286 run thất bại của Claude Code chạy model GLM-4.7. Ngưỡng hiệu chỉnh theo 5 lượt chia task. Mỗi số là trung bình 5 seed của model; ngoặc vuông là khoảng tin cậy 95% (bootstrap theo task).

**Đối chứng B0:** quy tắc đơn giản, báo động khi số step của run vượt một ngưỡng hiệu chỉnh cùng cách.

| Chỉ số | α | qcd-monitor (M1-6) | B0 |
|---|---|---|---|
| Run thành công bị báo động (báo động giả) | 0.1 | 6,1% [1,9; 11,3] | 6,9% [2,4; 12,9] |
| | 0.05 | 3,0% [0,6; 6,2] | 2,7% [0,6; 5,6] |
| Run thất bại bị báo động | 0.1 | 27,3% [19,5; 35,6] | 26,9% [18,7; 35,7] |
| | 0.05 | 15,9% [9,5; 23,2] | 12,4% [6,4; 19,3] |

- **Báo động trên run thất bại:** ở α = 0.1, kiểm định hoán vị cho p = 0,90. Dữ liệu không phân biệt được qcd-monitor với B0. Chưa có bằng chứng model bắt run thất bại tốt hơn quy tắc đếm step.
- **Model trong plugin là seed 0:**
  - ở seed này, tỷ lệ run thành công bị báo động là 7,5% ở α = 0.1;
  - tính theo trung bình các task, tỷ lệ này là 12,0%, lớn hơn α.
- **Ngưỡng trong plugin** hiệu chỉnh trên toàn bộ dữ liệu, nên không có ước lượng báo động giả trên dữ liệu chưa thấy cho đúng ngưỡng này.
- **Thời gian chạy hook** (Windows 10, CPU Intel 8 luồng, phát lại 20.421 lần gọi hook):
  - trung vị khoảng 137 ms mỗi tool call;
  - p95 là 176 ms, dưới ngân sách 200 ms;
  - B0 có p95 138 ms.
  - Lô có `Write` có p95 khoảng 190 ms, sát ngân sách.

## Dữ liệu trên máy

Thư mục: `~/.claude/plugins/data/qcd-monitor-qcd-monitor/`. Trên Windows là `%USERPROFILE%\.claude\plugins\data\qcd-monitor-qcd-monitor\`.

- **`log/<ngày>.jsonl`:** mỗi lần gọi hook ghi một dòng, gồm:
  - thời điểm, sự kiện, `session_id`, số run, số step;
  - dãy W, trạng thái báo động, chế độ, α;
  - sha256 của model, thời gian từng phần;
  - tên lớp lỗi, nếu có.

  Log không ghi nội dung code, lệnh hay prompt.
- **`state/<16 ký tự đầu của sha256(session_id)>.json`:** mỗi phiên một file, giữ những gì cần để tính đặc trưng giữa các lô. File này **có chứa văn bản thật**:
  - prompt của run hiện tại;
  - output tool gần nhất, tối đa 20.000 ký tự, có thể chứa mật khẩu hay token xuất hiện trong output;
  - các dòng lỗi, đường dẫn file, và tập token của code và prompt.
- File state không được cập nhật quá 7 ngày sẽ tự bị xóa khi có prompt mới.
- Bạn có thể xóa cả thư mục bất cứ lúc nào.

## Khi có sự cố

- **Lỗi bên trong hook:** hook bỏ qua lỗi (fail-open). Nó thoát bình thường, không in gì, không chặn agent, và chỉ ghi tên lớp lỗi vào trường `error` của log.
- **`/qcd-status` không hiện dòng trạng thái:**
  - kiểm `python --version`;
  - kiểm plugin đang bật trong `/plugin`;
  - xem dòng cuối của file log.
- **Claude Code chặn mọi prompt sau khi thư mục cache plugin bị xóa giữa phiên:** script hook không còn, nên Python thoát mã 2 và Claude Code chặn prompt. Tắt plugin (`/plugin`, tab **Installed**, chọn qcd-monitor rồi tắt), hoặc cập nhật plugin (**Update now**).
- **ruff chậm:** hook không có hạn chót bên trong. Lô nào vượt timeout 30 giây của hook sẽ bị Claude Code ngắt, và lô đó không được tính vào run.

## Cập nhật và gỡ cài đặt

- **Lấy bản mới:**
  ```
  /plugin marketplace update qcd-monitor
  ```
  rồi vào `/plugin`, tab **Installed**, chọn qcd-monitor, **Update now**. Bản mới chạy từ phiên sau, hoặc sau khi chạy `/reload-plugins`.
- **Tắt tạm thời:** `/plugin`, tab **Installed**, chọn qcd-monitor rồi tắt. Hoặc chạy trong terminal:
  ```
  claude plugin disable qcd-monitor@qcd-monitor
  ```
- **Gỡ cài đặt:**
  ```
  claude plugin uninstall qcd-monitor@qcd-monitor
  ```
  Lệnh này **xóa luôn thư mục dữ liệu** (log và state). Muốn giữ log thì thêm `--keep-data`.

## Giới hạn

- **Tỷ lệ báo động giả chỉ được kiểm trên một tổ hợp:** Claude Code + model GLM-4.7 + task Terminal-Bench 2. Khi bạn dùng Claude Code với model Claude, trên task của bạn, không có đảm bảo nào.
  - Mỗi run của Terminal-Bench 2 là một prompt dài. Khi dùng thật, thường có nhiều run ngắn giữa các prompt.
  - Trong nghiên cứu, điểm mỗi step có xu hướng tăng theo vị trí step trong run. Vì vậy run ngắn và run dài cho kết quả khác nhau.
- **Không kiểm được báo động giả khi dùng thật,** vì log không có nhãn run nào thành công, run nào thất bại.
- **α tính theo từng run.** Ngày có nhiều run sẽ có nhiều báo động giả. Ví dụ minh họa: 20 run thành công trong một ngày, với tỷ lệ 10%, cho trung bình 2 báo động giả.
- **Báo động nói về kết cục của cả run.** Nó không chỉ ra step nào bắt đầu sai.
- **Model:**
  - Model được train trên CodeTraceBench, sau khi bỏ các task trùng với Terminal-Bench 2. Việc bỏ này làm mất khoảng 40% số run thất bại có đánh dấu thời điểm hỏng, so với dữ liệu của phần chính bài báo.
  - Vì vậy model trong plugin khác các model ở phần chính của bài báo.
- **Tên task gần giống nhau:** vài cặp task giữa dữ liệu train và Terminal-Bench 2 có tên gần nhau, ví dụ `install-windows-xp` và `install-windows-3.11`. Nội dung của chúng có trùng nhau không thì chưa kiểm.
- **Quy tắc mới, không có trong dữ liệu train:** cách chuyển tool call của Claude Code sang dạng model hiểu, ví dụ Edit hoặc Read bị chặn được coi là step lỗi (thêm tiền tố `ERROR:`), và chuẩn hóa output của Read.
- **Thiếu ruff 0.15.12** thì không còn đảm bảo về tỷ lệ báo động giả.
- **Phạm vi theo dõi:**
  - bỏ qua tool call bên trong subagent;
  - không dùng lời của agent, chỉ dùng tool call;
  - mới thử trên Windows.
- **Python:** plugin mới chạy thử với Python 3.10.0 trên Windows 10. Các bản khác, cũ hơn hay mới hơn, chưa được kiểm.
  - Model lưu bằng `marshal` của Python 3.10. Theo [tài liệu Python](https://docs.python.org/3/library/marshal.html), định dạng `marshal` có thể đổi giữa các phiên bản.
  - Nếu Python của bạn không đọc được file model, hook bỏ qua lỗi (mục [Khi có sự cố](#khi-có-sự-cố)). Khi đó plugin không chấm điểm, không báo động và không hiện gì: trông như vẫn chạy nhưng thật ra không theo dõi.
  - Cách nhận ra: sau một prompt có tool call, `/qcd-status` vẫn báo `0 step`, và trường `error` của dòng log `PostToolBatch` không rỗng.

## Dữ liệu train và điều khoản

- **Model (M1-6, seed 0):**
  - LightGBM trên 49 đặc trưng;
  - train trên CodeTraceBench (Hugging Face `NJU-LINK/CodeTraceBench`, revision `aa213b8`), gồm quỹ đạo của 6 model nguồn, đã bỏ các task trùng Terminal-Bench 2;
  - dataset card ghi giấy phép MIT, nhưng repo dataset không có file LICENSE;
  - sha256 của model: `acae504962b4352e6e59f4ee54cccaf57748c27cb787aa5bbb5d07c7046434bb`.
- **Ngưỡng và đánh giá:** Hugging Face `harborframework/terminal-bench-2-leaderboard` (Apache-2.0), revision `572b261`, bài nộp `ClaudeCode__GLM-4.7`.
- **Điều khoản:** điều khoản sử dụng output của các model nguồn **chưa được kiểm xong**. Vì vậy bản này chỉ dùng thử nội bộ, và repo đang private. Mục này sẽ được cập nhật trước khi phát hành công khai.
