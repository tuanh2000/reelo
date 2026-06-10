// ===== Demo-only fixtures (NOT business data) =====
// These static series/outline samples exist ONLY for the offline demo
// (NEXT_PUBLIC_REQUIRE_AUTH=false, i.e. DEMO_FALLBACK === true) so the UI is
// browsable with no backend running. They must NEVER reach production: every
// access is gated behind `DEMO_FALLBACK` at the call site (see lib/data.ts).
// In prod (auth on) screens fetch real data from the API and these are unused.

import type { Series, OutlineItem } from "./data";

// Sample series list — only seeded into the dashboard/screens when DEMO_FALLBACK.
export const DEMO_SERIES: Series[] = [
  {
    id: "s1",
    name: "Bí ẩn các tôn giáo cổ đại",
    topic: "Tôn giáo & Lịch sử",
    skill: "religion",
    providers: { script: "claude", image: "gemini", voice: "eleven" },
    cover: "Đền thờ cổ, ánh sáng vàng",
    episodes: [
      { id: "e1", title: "Nguồn gốc của các vị thần", status: "published", dur: "9:42", views: "12K" },
      { id: "e2", title: "Đa thần giáo Lưỡng Hà", status: "published", dur: "11:08", views: "8.3K" },
      { id: "e3", title: "Tôn giáo Ai Cập cổ đại", status: "assembled", dur: "10:21" },
      { id: "e4", title: "Bí ẩn các giáo phái Hy Lạp", status: "assets" },
      { id: "e5", title: "Hỏa giáo & Zoroaster", status: "scripted" },
      { id: "e6", title: "Tín ngưỡng La Mã sơ khai", status: "draft" },
      { id: "e7", title: "Sự trỗi dậy của độc thần giáo", status: "draft" },
      { id: "e8", title: "Di sản còn lại ngày nay", status: "draft" },
    ],
  },
  {
    id: "s2",
    name: "Đế chế La Mã: Trỗi dậy & Sụp đổ",
    topic: "Lịch sử La Mã",
    skill: "story",
    providers: { script: "chatgpt", image: "kie", voice: "hf" },
    cover: "Đấu trường La Mã lúc hoàng hôn",
    episodes: [
      { id: "e1", title: "Lập quốc bên dòng Tiber", status: "published", dur: "8:55", views: "21K" },
      { id: "e2", title: "Cộng hòa & những cuộc chiến", status: "published", dur: "12:30", views: "15K" },
      { id: "e3", title: "Caesar vượt sông Rubicon", status: "published", dur: "13:12", views: "31K" },
      { id: "e4", title: "Augustus & thời hoàng kim", status: "assembled" },
      { id: "e5", title: "Khủng hoảng thế kỷ III", status: "scripted" },
      { id: "e6", title: "Sự sụp đổ của phương Tây", status: "draft" },
    ],
  },
  {
    id: "s3",
    name: "Vũ trụ trong 5 phút",
    topic: "Khoa học vũ trụ",
    skill: "explain",
    providers: { script: "gemini", image: "gemini", voice: "edge" },
    cover: "Thiên hà xoáy, tông xanh tím",
    episodes: [
      { id: "e1", title: "Lỗ đen thực sự là gì?", status: "scripted" },
      { id: "e2", title: "Vì sao bầu trời tối?", status: "draft" },
      { id: "e3", title: "Vật chất tối quanh ta", status: "draft" },
      { id: "e4", title: "Sự sống ngoài Trái Đất", status: "draft" },
    ],
  },
];

// Sample wizard outline — only seeded into the wizard when DEMO_FALLBACK.
export const DEMO_WIZARD_OUTLINE: OutlineItem[] = [
  { id: "w1", title: "Tập 1 — Phật giáo: Con đường trung đạo", desc: "Bối cảnh ra đời, Tứ diệu đế, ảnh hưởng tới châu Á.", pick: true },
  { id: "w2", title: "Tập 2 — Ấn Độ giáo: Vạn thần và luân hồi", desc: "Hệ thống thần linh, nghiệp báo, các trường phái.", pick: true },
  { id: "w3", title: "Tập 3 — Do Thái giáo: Giao ước cổ xưa", desc: "Lịch sử dân tộc, kinh Torah, truyền thống.", pick: true },
  { id: "w4", title: "Tập 4 — Kitô giáo lan tỏa toàn cầu", desc: "Từ một giáo phái nhỏ đến tôn giáo lớn nhất thế giới.", pick: true },
  { id: "w5", title: "Tập 5 — Hồi giáo: Năm trụ cột", desc: "Nguồn gốc, kinh Quran, sự bành trướng.", pick: false },
];
