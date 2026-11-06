import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import PwaRegister from "../components/PwaRegister";
import "./globals.css";

export const metadata: Metadata = {
  title: "한자 튜터",
  description: "초등학생 한자 급수 학습·문해력 챗봇",
  manifest: "/manifest.webmanifest",
  icons: { icon: "/icon-192.png", apple: "/icon-192.png" },
  appleWebApp: { capable: true, title: "한자튜터" },
};

export const viewport: Viewport = { themeColor: "#0f766e", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
      <body>
        <PwaRegister />
        {children}
      </body>
    </html>
  );
}
