# System Prompt Review Checklist
> **Owner:** QE Lead  
> **Review cycle:** Mỗi Sprint hoặc khi có thay đổi tool/process  
> **File cần review:** `system_prompt.txt`

---

## Changelog
| Ngày | Người thực hiện | Thay đổi |
|------|----------------|----------|
| 2026-06-11 | QE Lead | Fix dòng 70: xóa Xray plugin sai, cập nhật vai trò Jira = Bug tracking & Task management, thêm rule 9 cấm đề cập Xray |

---

## Checklist Review Định Kỳ

### 1. Danh sách công cụ (Tools)
- [ ] Jira: vai trò là **Bug tracking & Task management** (KHÔNG phải testcase management)
- [ ] Jira: **KHÔNG** có mention Xray plugin
- [ ] TestLink: được ghi là công cụ **duy nhất** để quản lý testcase chi tiết
- [ ] Confluence: đúng URL `https://confluence.zalopay.vn/`
- [ ] Không có công cụ nào bị ghi sai vai trò hoặc không còn dùng nữa

### 2. Quy tắc bắt buộc (Rules)
- [ ] Rule "CÔNG CỤ BỊ CẤM ĐỀ CẬP" liệt kê đúng các tool team không dùng
- [ ] Các rule mới (nếu có) đã được thêm vào danh sách đánh số

### 3. Product Domain
- [ ] Danh sách sản phẩm (P2P, IBFT, Send Bill, Lì Xì) vẫn còn đúng
- [ ] Danh sách dependencies (Cashier, Promotion, MMF, Lending) vẫn còn đúng
- [ ] Platform hợp lệ (ZPA iOS, ZPA Android, ZPI, ZMP) không thay đổi

### 4. Bug Report Format
- [ ] Template markdown vẫn khớp với format thực tế đang dùng

### 5. Testcase Rules
- [ ] Các mock amount đặc biệt (`31000`, `131001`→`131005`, `161000`) vẫn còn đúng
- [ ] gRPC service name `MTIbftAPI` vẫn còn đúng

### 6. Links tài liệu
- [ ] Tất cả URL Confluence vẫn còn accessible (không bị đổi hoặc xóa)

---

## Regression Test Cases (Chạy sau mỗi lần cập nhật system_prompt.txt)

| # | Câu hỏi test | Expected Answer (từ khóa kiểm tra) |
|---|-------------|-------------------------------------|
| T1 | "Công cụ quản lý testcase là gì?" | `TestLink` ✅ — KHÔNG có `Xray` ❌ |
| T2 | "Jira dùng để làm gì?" | `bug tracking`, `task management` ✅ — KHÔNG có `Xray` ❌ |
| T3 | "Quy trình làm việc và công cụ chính là gì?" | Bảng tool không có Xray ❌ |
| T4 | "Tôi có nên dùng Xray để viết testcase không?" | Bot từ chối, hướng dẫn dùng `TestLink` ✅ |
| T5 | "Format bug report?" | Template markdown đúng chuẩn ✅ |
| T6 | "Sản phẩm của QE_Consumer team gồm những gì?" | P2P, IBFT, Send Bill, Lì Xì ✅ |

---

## Quy trình cập nhật system_prompt.txt (Bắt buộc tuân thủ)

```
1. KHÔNG sửa trực tiếp trên file production khi chưa review
2. Tạo branch mới → sửa → tạo PR → QE Lead review → merge
3. Chạy toàn bộ Regression Test Cases ở trên sau khi merge
4. Cập nhật Changelog trong file này
```
