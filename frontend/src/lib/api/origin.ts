export function getPublicApiBaseUrl() {
  return (process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");
}
