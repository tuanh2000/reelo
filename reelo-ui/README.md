# Reelo UI

Frontend prototype cho **Reelo Studio** — nền tảng dựng video YouTube bằng AI.
Dựng lại high-fidelity từ bản design handoff (`design_handoff_reelo_studio`) bằng
**Next.js (App Router) + TypeScript + Tailwind**.

## Chạy dự án

```bash
npm install
npm run dev      # http://localhost:3000
```

Build production:

```bash
npm run build && npm start
```

## Trạng thái

- ✅ **Frontend đầy đủ** — 7 màn hình, mock data, điều hướng client-side, theme sáng/tối,
  nền động, logo 3D tương tác. Pixel-faithful với bản design.
- ⏳ **Backend để trống** — tất cả điểm cần backend được stub trong [`lib/api.ts`](./lib/api.ts)
  và đánh dấu `TODO(backend)` ngay tại chỗ gọi (chat LLM, pipeline tạo asset, suy luận style,
  publish YouTube, lưu API key). Hiện các tương tác chạy bằng mô phỏng phía client.

## Cấu trúc

```
app/
  layout.tsx        # root layout, fonts, <html data-theme>
  page.tsx          # mount <App/>
  globals.css       # design system (token + component CSS) — nguồn chân lý về style
components/
  app-root.tsx      # routing nội bộ + theme + toast + nền động
  shell.tsx         # Sidebar + Topbar
  ui.tsx            # UI kit: Icon, Button, Badge, Card, Progress, Placeholder, ...
  logo.tsx          # MiniMark, Wordmark, Logo3D (3D tương tác)
screens/            # 7 màn hình: dashboard, wizard, setup, style, workspace, review, project
lib/
  data.ts           # mock data + types (Series, Episode, Skill, Provider, ...)
  api.ts            # STUB các endpoint backend (sẽ wire sau)
```

## Các màn hình

| Route | Màn hình |
|---|---|
| `dashboard` | Bảng điều khiển — danh sách series |
| `wizard` | Trợ lý tạo series (chat + dàn ý) |
| `setup` | Skill & Provider (BYOK) |
| `style` | Style Studio |
| `workspace` | Xưởng kịch bản (pipeline + editor + tinh chỉnh văn phong) |
| `review` | Duyệt & Xuất bản (player + metadata + 1-click publish) |
| `project` | Chi tiết series (tiến độ + resume) |

Điều hướng hiện dùng state nội bộ (`components/app-root.tsx`). Khi thêm URL thật,
thay bằng Next.js router.

## Ghi chú kỹ thuật

- Hệ design dùng CSS biến (`--brand`, `--surface`, …) trong `globals.css`; Tailwind đã
  được cấu hình map sang các token này (xem `tailwind.config.ts`) để dùng cho component mới.
- Icon dùng `lucide-react` (component `Icon` tra cứu theo tên kebab-case).
