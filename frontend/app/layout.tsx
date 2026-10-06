import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Qanoon | Pakistan Law Assistant",
  description:
    "Understand Pakistani law with clear answers grounded in cited legal sources.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
