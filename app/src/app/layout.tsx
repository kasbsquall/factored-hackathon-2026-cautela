import type { Metadata, Viewport } from "next";
import { Atkinson_Hyperlegible_Next, Bricolage_Grotesque, IBM_Plex_Mono } from "next/font/google";
import { Providers } from "@/components/providers";
import "./globals.css";

const bricolage = Bricolage_Grotesque({
  subsets: ["latin", "latin-ext"],
  axes: ["opsz"],
  variable: "--font-bricolage",
  display: "swap",
});

const atkinson = Atkinson_Hyperlegible_Next({
  subsets: ["latin", "latin-ext"],
  variable: "--font-atkinson",
  display: "swap",
});

const plexMono = IBM_Plex_Mono({
  subsets: ["latin", "latin-ext"],
  weight: ["400", "500"],
  variable: "--font-plex-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: { default: "Cautela", template: "%s · Cautela" },
  description: "Dispute intake that acts on what it can verify, asks when unsure, and hands the rest to a person.",
};

export const viewport: Viewport = {
  themeColor: "#0F201C",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${bricolage.variable} ${atkinson.variable} ${plexMono.variable}`}>
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
