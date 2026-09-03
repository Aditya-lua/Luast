import type { Metadata } from "next";
import type { ReactNode } from "react";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Luast Deobfuscator — engine, web UI & Discord bot",
  description:
    "Multi-pass static deobfuscator for luast-style Luau obfuscation: constant pools, control-flow flattening, opaque branches. Web UI, REST API and Discord slash commands.",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="min-h-screen bg-zinc-950 text-zinc-100 antialiased">
        <header className="sticky top-0 z-30 border-b border-zinc-800/80 bg-zinc-950/80 backdrop-blur">
          <div className="mx-auto flex max-w-7xl items-center justify-between px-4 py-3 sm:px-6">
            <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
              <span className="grid h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-violet-500 to-fuchsia-500 text-sm font-bold text-white shadow-lg shadow-violet-500/30">
                λ
              </span>
              <span>
                luast<span className="text-violet-400">.deobf</span>
              </span>
            </Link>
            <nav className="flex items-center gap-1 text-sm text-zinc-400">
              <Link href="/" className="rounded-md px-3 py-1.5 hover:bg-zinc-800 hover:text-zinc-100">
                Deobfuscate
              </Link>
              <Link href="/jobs" className="rounded-md px-3 py-1.5 hover:bg-zinc-800 hover:text-zinc-100">
                History
              </Link>
              <Link href="/discord" className="rounded-md px-3 py-1.5 hover:bg-zinc-800 hover:text-zinc-100">
                Discord bot
              </Link>
              <Link href="/docs" className="rounded-md px-3 py-1.5 hover:bg-zinc-800 hover:text-zinc-100">
                Engine docs
              </Link>
            </nav>
          </div>
        </header>
        {children}
        <footer className="mt-16 border-t border-zinc-800/80 py-8 text-center text-xs text-zinc-500">
          Static analysis engine for Luau. Identifiers and comments removed by obfuscation are reconstructed heuristically, not recovered.
        </footer>
      </body>
    </html>
  );
}
