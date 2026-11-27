import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { BannerGroup, BannerSlot } from "../storefront-banners";
import type { ContentBanner } from "../../types";

const { get } = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock("../../api", () => ({ CMS_BASE: "/api/content/", publicCmsClient: { get } }));

function banner(overrides: Partial<ContentBanner> = {}): ContentBanner {
  return {
    id: 1, placement: "category_top", title: "Print collection", subtitle: "Shop the new series",
    status: "published", published_at: null, starts_at: null, ends_at: null, is_visible: true,
    is_clickable: true, link_url: "/products", cta_text: "See prints", order: 1,
    image: "/media/desktop.png", mobile_image: "/media/mobile.webp",
    background_color: "#faf0e5", text_color: "#172031", ...overrides,
  };
}

describe("storefront banners", () => {
  beforeEach(() => get.mockReset());

  it("scopes, deduplicates, and exposes responsive image sources and the CTA", () => {
    const item = banner();
    const { container } = render(<BannerGroup placement="category_top" banners={[
      item, item, banner({ id: 2, placement: "global", title: "Wrong slot" }),
      banner({ id: 3, is_visible: false, title: "Hidden" }),
    ]} />);
    expect(screen.getAllByText("Print collection")).toHaveLength(1);
    expect(screen.queryByText("Wrong slot")).toBeNull();
    expect(screen.queryByText("Hidden")).toBeNull();
    expect(screen.getByRole("link", { name: "See prints" })).toHaveAttribute("href", "/products");
    expect(container.querySelector("source")).toHaveAttribute("srcset", expect.stringContaining("/media/mobile.webp"));
    expect(container.querySelector("img")).toHaveAttribute("loading", "lazy");
  });

  it("keeps non-clickable text and drops unsafe links and image URLs", () => {
    const { container } = render(<BannerGroup placement="category_top" banners={[
      banner({ is_clickable: false, image: "data:text/html,markup" }),
      banner({ id: 2, title: "Unsafe CTA", link_url: "https://username:password@example.invalid", image: "//example.invalid/x" }),
    ]} />);
    expect(screen.getByText("Print collection")).toBeVisible();
    expect(screen.queryByRole("link")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
  });

  it("coalesces requests and retires stale category responses", async () => {
    let resolve!: (result: { data: ContentBanner[] }) => void;
    get.mockImplementationOnce(() => new Promise((callback) => { resolve = callback; }))
      .mockResolvedValueOnce({ data: [banner({ title: "Current category" })] });
    const { rerender } = render(<><BannerSlot placement="category_top" target="11" /><BannerSlot placement="category_top" target="11" /></>);
    expect(get).toHaveBeenCalledTimes(1);
    rerender(<BannerSlot placement="category_top" target="12" />);
    await screen.findByText("Current category");
    await act(async () => resolve({ data: [banner({ title: "Previous category" })] }));
    expect(screen.queryByText("Previous category")).toBeNull();
    expect(get.mock.lastCall?.[1]).toEqual({ params: { placement: "category_top", category: "12" } });
  });

  it("keeps checkout usable when the CMS fails and gracefully handles empty data", async () => {
    get.mockRejectedValueOnce(new Error("Unavailable")).mockResolvedValueOnce({ data: [] });
    const { rerender } = render(<><BannerSlot placement="checkout" /><button>Place order</button></>);
    await waitFor(() => expect(get).toHaveBeenCalledTimes(1));
    expect(screen.getByRole("button", { name: "Place order" })).toBeEnabled();
    expect(screen.queryByRole("region", { name: "Checkout information" })).toBeNull();
    rerender(<BannerSlot placement="global" />);
    await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
    expect(screen.queryByRole("region", { name: "Storefront promotions" })).toBeNull();
  });
});
