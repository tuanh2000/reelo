# Prompt thiết kế UI — AI YouTube Video Generator Platform

> Copy nguyên khối phần dưới và đưa cho Claude (Claude.ai với Artifacts, hoặc Claude Code)
> để dựng prototype web clickable đầy đủ các màn hình.

---

Bạn là một senior product designer kiêm frontend engineer. Hãy thiết kế và dựng một
PROTOTYPE WEB clickable (React + Tailwind, single-page, dùng mock data — KHÔNG cần backend
thật) cho một sản phẩm SaaS tên là "AI YouTube Video Generator Platform".

## Sản phẩm làm gì
Người dùng chỉ cần đưa một ý tưởng/chủ đề tổng quát, nền tảng dùng AI để làm toàn bộ công
việc dựng video YouTube: lên ý tưởng series → viết kịch bản → tạo giọng đọc → tạo hình ảnh/
video → ghép thành file .mp4 → sinh metadata → 1 click upload lên YouTube. User chỉ duyệt ở
bước cuối. Điểm khác biệt: user được tự chọn nhà cung cấp AI cho từng khâu (có thể chọn các
dịch vụ miễn phí), và chọn "skill" (template chủ đề do chúng tôi maintain, ví dụ skill làm
video tôn giáo).

## Người dùng mục tiêu
Nhà sáng tạo nội dung YouTube không rành kỹ thuật, muốn sản xuất video hàng loạt theo series.

## Các MÀN HÌNH cần thiết kế (tạo navigation/route giữa chúng, có thể click qua lại)

1. **Landing / Dashboard** — danh sách các Project (kênh/series) của user dưới dạng card,
   mỗi card hiển thị: tên series, chủ đề, skill đang dùng, thanh tiến độ (vd "2/8 tập đã xong"),
   nút "Tạo tập tiếp theo". Nút lớn "+ Tạo series mới".

2. **New Series Wizard (chat-based)** — màn hình chat 2 cột:
   - Trái: hội thoại với AI. User nhập chủ đề tổng quát ("tôn giáo", "lịch sử La Mã"...),
     AI hỏi lại và đề xuất một DANH SÁCH các tập video (mỗi tập có tiêu đề + mô tả ngắn).
     User chat để chỉnh, AI cập nhật danh sách.
   - Phải: panel "Series Outline" hiển thị danh sách tập dạng list có thể sửa/xóa/sắp xếp,
     mỗi tập có checkbox chọn để sản xuất. Nút "Chốt series & Lưu".

3. **Skill & Provider Setup** — màn hình chọn cấu hình cho series:
   - Chọn Skill (card chọn 1 trong nhiều template: "Tôn giáo học thuật", "Storytelling",
     "Explainer"... — hiện tại để 3-4 card mock).
   - Chọn Provider cho từng khâu, dạng các nhóm dropdown/segmented:
     • Research & Script: ChatGPT / Claude / Gemini / DeepSeek
     • Image/Video: Google AI Studio / kie.ai / ...
     • Voice (TTS): ElevenLabs / HuggingFace / ...
     Mỗi lựa chọn hiện badge "Free tier" hoặc "Cần API key". Có chỗ nhập/lưu API key (BYOK).

4. **Style Studio** — màn hình chọn phong cách hình ảnh: khu vực upload ảnh mẫu (drag & drop,
   hiển thị thumbnail), bên cạnh là preview "Style được suy ra" (palette màu + mô tả style text).
   Vài preset style để chọn nhanh.

5. **Script Workspace (chat + editor)** — màn hình sản xuất 1 tập:
   - Editor kịch bản ở giữa (các đoạn ngăn cách rõ ràng, mỗi đoạn gắn với 1 hình ảnh).
   - Panel chat AI bên phải để tinh chỉnh "văn phong" (giọng văn, độ dài, tone).
   - Cột trái hiển thị các bước pipeline với trạng thái: Script ✓ → Voice → Images →
     Assemble → Review (dạng stepper/timeline có icon trạng thái).

6. **Generation / Progress** — màn hình theo dõi quá trình tạo asset: danh sách job
   (Voice, Image 1..N, Render mp4) với progress bar, trạng thái (đang chạy / xong / lỗi +
   nút retry từng cái). Hiển thị preview audio (player) và thumbnail các ảnh khi xong.

7. **Final Review & Publish** — màn hình duyệt cuối: trình phát video .mp4 (mock player),
   form metadata YouTube (tiêu đề, mô tả, tags, thumbnail chọn được), và một nút lớn nổi bật
   "Upload lên YouTube". Sau khi bấm hiện trạng thái thành công.

8. **Project Detail / Series Progress** — xem 1 series: danh sách tất cả các tập với trạng
   thái từng tập (Draft / Scripted / Assets / Assembled / Published), cho phép mở lại tập
   dở dang để làm tiếp (thể hiện rõ tính năng "resume").

## Yêu cầu thiết kế (UI/UX)
- Phong cách: hiện đại, sạch sẽ, sáng sủa, thân thiện với người không rành kỹ thuật;
  bo góc mềm, nhiều khoảng trắng, màu accent tươi (gợi ý: tím/indigo làm màu chủ đạo).
- Responsive (ưu tiên desktop), có sidebar điều hướng trái cố định + topbar.
- Dùng component nhất quán: card, button, badge, stepper, modal, toast, progress bar,
  chat bubble, file uploader. Có thể dùng lucide-react cho icon.
- Mock data sẵn (1-2 series mẫu, vài tập có trạng thái khác nhau) để click qua lại thấy như
  app thật. Các nút bấm chuyển màn hình/đổi state thật trên prototype.
- Nhấn mạnh được 4 điểm cốt lõi: (1) tạo series bằng chat, (2) lưu & resume trạng thái,
  (3) tự chọn provider/skill, (4) tự động dựng + 1 click publish.

## Output
- Toàn bộ trong 1 artifact React chạy được, điều hướng bằng state nội bộ (không cần router lib
  nếu phức tạp). Tách component gọn gàng, đặt tên rõ. Ưu tiên đẹp và mượt khi click thử.
- Sau khi dựng xong, mô tả ngắn gọn cách di chuyển giữa các màn hình.

Hãy bắt đầu bằng việc đề xuất nhanh sitemap + design system (màu, font, spacing) trong 5-6 dòng,
rồi dựng prototype.
