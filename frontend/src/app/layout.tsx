import "@/styles/globals.css";

import { type Metadata, type Viewport } from "next";
import localFont from "next/font/local";

import { ThemeProvider } from "@/components/theme-provider";
import { APP_NAME } from "@/core/brand";
import { DEFAULT_LOCALE } from "@/core/i18n/locale";

// Self-hosted (SIL OFL 1.1, see fonts/LICENSE.md) so production builds never
// fetch from Google Fonts: Docker builds behind mirrors and air-gapped hosts
// cannot reach it. Latin subsets only; Chinese text uses the CJK faces in the
// --font-sans stack (globals.css).
const dmSans = localFont({
  src: "./fonts/dm-sans-latin.woff2",
  weight: "100 1000",
  style: "normal",
  display: "swap",
  variable: "--font-dm-sans",
});

const jetBrainsMono = localFont({
  src: "./fonts/jetbrains-mono-latin.woff2",
  weight: "100 800",
  style: "normal",
  display: "swap",
  preload: false,
  adjustFontFallback: false,
  variable: "--font-jetbrains-mono",
});

export const metadata: Metadata = {
  title: { default: APP_NAME, template: `%s - ${APP_NAME}` },
  description: "GGWork workbench",
  // Private, login-only workbench; next.config.js also sends X-Robots-Tag.
  robots: { index: false, follow: false },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f7f9fb" },
    { media: "(prefers-color-scheme: dark)", color: "#0c1115" },
  ],
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang={DEFAULT_LOCALE}
      className={`${dmSans.variable} ${jetBrainsMono.variable}`}
      suppressContentEditableWarning
      suppressHydrationWarning
    >
      <body>
        <ThemeProvider attribute="class" enableSystem disableTransitionOnChange>
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}
