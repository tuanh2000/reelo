import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Reelo Studio — AI YouTube Video Generator",
  description:
    "Reelo — dựng video YouTube hoàn chỉnh (hình ảnh & âm thanh) hoàn toàn bằng AI. Một ý tưởng, cả một series.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi" data-theme="light" suppressHydrationWarning>
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
      </head>
      <body>{children}</body>
    </html>
  );
}
