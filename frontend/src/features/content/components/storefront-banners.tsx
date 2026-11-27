"use client";

import { ArrowRight } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";
import { buttonStyles } from "@/components/ui/button";
import { getImageUrl } from "@/lib/media/image-url";
import { CMS_BASE, publicCmsClient } from "../api";
import { safeBannerLink, safeImageUrl } from "../safety";
import type { ContentBanner } from "../types";

type Placement = ContentBanner["placement"];
const labels: Record<Placement, string> = {
  home_hero: "Homepage feature", home_top: "Homepage highlights", home_middle: "Marketplace collections",
  category_top: "Category highlights", product_detail: "Product highlights", checkout: "Checkout information",
  global: "Storefront promotions",
};

export function colorIfHex(value: string | undefined, fallback: string) {
  return value && /^#[0-9a-f]{6}$/i.test(value) ? value : fallback;
}

export function mediaImage(value: unknown) {
  const href = safeImageUrl(value);
  return href ? getImageUrl(href) : null;
}

export function BannerPicture({ banner, eager = false }: { banner: ContentBanner; eager?: boolean }) {
  const desktop = mediaImage(banner.image);
  const mobile = mediaImage(banner.mobile_image);
  const [imageState, setImageState] = useState<"responsive" | "desktop" | "failed">("responsive");
  if (!desktop || imageState === "failed") return null;
  return <picture className="block size-full">
    {mobile && imageState === "responsive" && <source media="(max-width: 639px)" srcSet={mobile} />}
    <img src={desktop} alt="" loading={eager ? "eager" : "lazy"} fetchPriority={eager ? "high" : undefined}
      className="size-full object-contain" onError={() => setImageState(mobile && imageState === "responsive" ? "desktop" : "failed")} />
  </picture>;
}

export function BannerGroup({ placement, banners, className = "" }: {
  placement: Placement; banners: ContentBanner[]; className?: string;
}) {
  const seen = new Set<number>();
  const visible = banners.filter((item) => {
    if (item.placement !== placement || !item.is_visible || seen.has(item.id)) return false;
    seen.add(item.id);
    return true;
  });
  const compact = placement === "global" || placement === "checkout";
  if (!visible.length) return null;
  return <section aria-label={labels[placement]} data-placement={placement}
    className={`grid min-w-0 gap-3 ${compact ? "grid-cols-1" : "sm:grid-cols-2"} ${className}`}>
    {visible.map((banner) => {
      const href = banner.is_clickable ? safeBannerLink(banner.link_url) : null;
      return <article key={banner.id}
        className={`grid min-w-0 overflow-hidden rounded-2xl border border-line ${compact ? "grid-cols-[6rem_minmax(0,1fr)] items-center gap-3 p-3 sm:grid-cols-[9rem_minmax(0,1fr)]" : ""}`}
        style={{ backgroundColor: colorIfHex(banner.background_color, "#f4f0e9"), color: colorIfHex(banner.text_color, "#172031") }}>
        <div className={`min-w-0 overflow-hidden rounded-xl ${compact ? "aspect-[2/1]" : "aspect-[5/4] sm:aspect-[16/7]"}`}>
          <BannerPicture key={`${banner.image}|${banner.mobile_image}`} banner={banner} />
        </div>
        <div className={`flex min-w-0 flex-wrap items-center justify-between gap-2 break-words ${compact ? "" : "p-5"}`}>
          <div className="min-w-0">
            <h2 className={compact ? "text-sm font-semibold" : "mb-display text-2xl"}>{banner.title}</h2>
            {banner.subtitle && <p className="mt-1 text-sm">{banner.subtitle}</p>}
          </div>
          {href && <Link href={href} prefetch={false} className={buttonStyles({ variant: "secondary", size: "small", className: "max-w-full whitespace-normal rounded-full" })}
            target={href.startsWith("/") ? undefined : "_blank"} rel={href.startsWith("/") ? undefined : "noopener noreferrer"}>
            {banner.cta_text || "Learn more"}<ArrowRight size={15} aria-hidden="true" />
          </Link>}
        </div>
      </article>;
    })}
  </section>;
}

// Deduplicate concurrent requests (including Strict Mode mounts). Published
// changes are checked on each new mount or contextual target change.
const pending = new Map<string, Promise<ContentBanner[]>>();
function fetchSlot(placement: Placement, target?: string) {
  const key = `${placement}:${target || ""}`;
  const existing = pending.get(key);
  if (existing) return existing;
  const params = { placement, ...(target ? { [placement === "category_top" ? "category" : "product"]: target } : {}) };
  const request = publicCmsClient.get<ContentBanner[]>(`${CMS_BASE}banners/storefront/`, { params })
    .then(({ data }) => Array.isArray(data) ? data : [])
    .finally(() => pending.delete(key));
  pending.set(key, request);
  return request;
}

export function BannerSlot({ placement, target, className }: { placement: Placement; target?: string; className?: string }) {
  const [result, setResult] = useState<{ key: string; banners: ContentBanner[] }>();
  const key = `${placement}:${target || ""}`;
  const valid = placement !== "category_top" && placement !== "product_detail" || Boolean(target && /^[1-9]\d*$/.test(target));
  useEffect(() => {
    if (!valid) return;
    let current = true;
    void fetchSlot(placement, target)
      .then((banners) => { if (current) setResult({ key, banners }); })
      .catch(() => { if (current) setResult({ key, banners: [] }); });
    return () => { current = false; };
  }, [placement, target, key, valid]);
  return valid && result?.key === key ? <BannerGroup placement={placement} banners={result.banners} className={className} /> : null;
}
