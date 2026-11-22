import type { Metadata, Viewport } from "next";
import "./globals.css";
import Shell from "@/components/Shell";

export const metadata: Metadata = {
  title: "Smart Money Radar",
  description: "Smart Money Wallets trên Pump.fun / Solana — phân tích on-chain, chỉ đọc dữ liệu",
  appleWebApp: { capable: true, title: "SM Radar", statusBarStyle: "black-translucent" },
  robots: { index: false, follow: false },
};
export const viewport: Viewport = {
  width: "device-width", initialScale: 1, viewportFit: "cover",
  themeColor: [{ media: "(prefers-color-scheme: dark)", color: "#020617" }, { media: "(prefers-color-scheme: light)", color: "#ffffff" }],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: `try{var t=localStorage.getItem('theme');if(t==='dark'||(!t&&matchMedia('(prefers-color-scheme: dark)').matches))document.documentElement.classList.add('dark')}catch(e){}` }} />
      </head>
      <body><Shell>{children}</Shell></body>
    </html>
  );
}
