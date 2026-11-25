import { getPublicApiBaseUrl } from "@/lib/api/origin";

export function getImageUrl(path: string | null | undefined) {
  if (!path) {
    return null;
  }

  if (path.startsWith("http://") || path.startsWith("https://")) {
    return path;
  }

  return `${getPublicApiBaseUrl()}${path}`;
}
