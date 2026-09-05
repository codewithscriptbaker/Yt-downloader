import { Suspense } from "react";
import { AuthProvider } from "@/components/AuthProvider";
import { ThemeScript } from "@/components/ThemeScript";
import "./globals.css";

export const metadata = {
  title: "MediaPort — Media Downloader",
  description:
    "Download YouTube, TikTok, Instagram, and Facebook media. Paste a link and grab the file.",
  applicationName: "MediaPort",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <ThemeScript />
      </head>
      <body>
        <AuthProvider>
          <Suspense fallback={null}>{children}</Suspense>
        </AuthProvider>
      </body>
    </html>
  );
}
