import { afterEach, describe, expect, it, vi } from "vitest";
import { generateMetadata } from "./page";
import { getPublicApiBaseUrl } from "@/lib/api/origin";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("server-rendered CMS pages", () => {
  it.each([
    ["http://web:8000/", "http://web:8000"],
    [undefined, "http://localhost:8000"],
  ])("resolves the API origin with INTERNAL_API_BASE_URL=%s", async (internal, expected) => {
    vi.stubEnv("NEXT_PUBLIC_API_BASE_URL", "http://localhost:8000");
    vi.stubEnv("INTERNAL_API_BASE_URL", internal);
    const fetchPage = vi.fn().mockResolvedValue({ status: 404 });
    vi.stubGlobal("fetch", fetchPage);

    await generateMetadata({ params: Promise.resolve({ slug: "terms" }) });

    expect(fetchPage).toHaveBeenCalledWith(`${expected}/api/content/pages/terms/`, expect.anything());
    expect(getPublicApiBaseUrl()).toBe("http://localhost:8000");
  });
});
