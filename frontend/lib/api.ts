const configuredApiBase = process.env.NEXT_PUBLIC_API_BASE_URL?.trim();
const localDevelopmentApiBase =
  process.env.NODE_ENV === "development" ? "http://localhost:8000" : "";

export const apiBase = (
  configuredApiBase || localDevelopmentApiBase
).replace(/\/+$/, "");

export function apiUrl(path: string): string {
  if (!apiBase) {
    throw new Error(
      "The backend URL is not configured. Set NEXT_PUBLIC_API_BASE_URL in the Vercel project settings, then redeploy.",
    );
  }
  return `${apiBase}${path}`;
}
