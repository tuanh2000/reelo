# Reelo

> Dựng video YouTube hoàn chỉnh — hình ảnh & âm thanh — hoàn toàn bằng AI.

## Ý tưởng cốt lõi

**Reelo** là một sản phẩm SaaS giúp nhà sáng tạo nội dung YouTube sản xuất video
hàng loạt mà không cần kỹ năng kỹ thuật. Người dùng chỉ cần đưa ra **một ý tưởng
gốc**, phần còn lại do AI lo.

Trải nghiệm trung tâm là một **trợ lý ảo (AI assistant)**: người dùng trò chuyện
với trợ lý để quyết định nội dung video. User đưa ý tưởng tổng quát → AI đề xuất
nội dung & kịch bản → user duyệt/chỉnh qua hội thoại → đầu ra là **video YouTube
hoàn chỉnh** về cả hình ảnh lẫn âm thanh.

### Người dùng chốt, AI làm hết

1. **Ý tưởng → Đề xuất** — User nhập chủ đề tổng quát ("tôn giáo", "lịch sử La Mã"…),
   AI đề xuất danh sách các tập video và kịch bản gợi ý.
2. **Hội thoại để tinh chỉnh** — User chat với trợ lý để chỉnh nội dung, văn phong,
   số tập, tone… AI cập nhật theo thời gian thực.
3. **AI dựng tự động** — Sau khi user chốt, AI thực hiện toàn bộ pipeline:
   viết kịch bản → tạo giọng đọc (TTS) → tạo hình ảnh/video → ghép thành `.mp4`
   → sinh metadata YouTube.
4. **Duyệt cuối & Publish** — User chỉ duyệt ở bước cuối, rồi 1-click upload lên YouTube.

## Điểm khác biệt

- **Tạo series bằng chat** — Toàn bộ việc lên ý tưởng & kịch bản diễn ra qua hội
  thoại tự nhiên với trợ lý ảo, không cần biểu mẫu phức tạp.
- **Lưu & Resume** — Mọi project/series và tập video được lưu trạng thái; user có
  thể quay lại làm tiếp tập đang dở bất cứ lúc nào.
- **Tự chọn Provider/Skill** — User chọn nhà cung cấp AI cho từng khâu (Research &
  Script, Image/Video, Voice/TTS), bao gồm cả các dịch vụ free tier hoặc BYOK
  (mang API key của riêng mình). "Skill" là các template chủ đề do Reelo maintain
  (ví dụ: skill làm video tôn giáo, storytelling, explainer…).
- **Tự động dựng + 1-click Publish** — Từ ý tưởng đến video `.mp4` hoàn chỉnh và
  đăng lên YouTube, user chỉ can thiệp ở khâu duyệt cuối.

## Đối tượng mục tiêu

Nhà sáng tạo nội dung YouTube **không rành kỹ thuật**, muốn sản xuất video hàng loạt
theo series một cách nhất quán và nhanh chóng.

## Trạng thái dự án

Đang ở giai đoạn dựng **prototype UI** (React + Tailwind, mock data) dựa trên thiết
kế trong [`design-prompt.md`](./design-prompt.md). Kiến trúc hệ thống được phác thảo
trong [`architecture.excalidraw`](./architecture.excalidraw).
