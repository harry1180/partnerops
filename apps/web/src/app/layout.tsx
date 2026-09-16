import type { Metadata } from "next";
import "./globals.css";
import { AppProvider } from "@/lib/app-state";

export const metadata: Metadata = {
  title: "Cloud PartnerOps",
  icons: { icon: "/icon.svg" },
  description: "PartnerOps and cloud financial management for MSPs, resellers and distributors",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <AppProvider>{children}</AppProvider>
      </body>
    </html>
  );
}
