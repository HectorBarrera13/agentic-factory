import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatTime(epochSeconds: number): string {
  return new Date(epochSeconds * 1000).toLocaleTimeString("es", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function languageFor(path: string): string {
  const ext = path.split(".").pop()?.toLowerCase() ?? "";
  const map: Record<string, string> = {
    py: "python",
    ts: "typescript",
    tsx: "typescript",
    js: "javascript",
    jsx: "javascript",
    mjs: "javascript",
    json: "json",
    md: "markdown",
    toml: "ini",
    ini: "ini",
    cfg: "ini",
    yml: "yaml",
    yaml: "yaml",
    html: "html",
    css: "css",
    sh: "shell",
    ps1: "powershell",
    sql: "sql",
    txt: "plaintext",
  };
  return map[ext] ?? "plaintext";
}
