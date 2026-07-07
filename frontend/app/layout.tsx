import type { Metadata } from "next";
import { Noto_Sans_Devanagari } from "next/font/google";
import "./globals.css";

const devanagari = Noto_Sans_Devanagari({
  subsets: ["devanagari", "latin"],
  weight: ["400", "500", "600"],
  display: "swap",
});

export const metadata: Metadata = {
  title: "Swastha AI — Hospital Voice Receptionist",
  description: "Multi-agent AI voice receptionist for Indian hospitals, powered by Sarvam AI.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={devanagari.className}>
      <body>{children}</body>
    </html>
  );
}
