"use client";

/* eslint-disable @next/next/no-img-element */
import { Shapes } from "lucide-react";
import { useState } from "react";
import { mediaImage } from "@/features/content/components/storefront-banners";

export function CategoryArtwork({ image }: { image?: string | null }) {
  const url = mediaImage(image);
  const [failed, setFailed] = useState<string | null>(null);
  return <div className="absolute inset-0 -z-10 grid place-items-center overflow-hidden bg-gradient-to-br from-ember-100 via-surface to-slate-300" aria-hidden="true">
    {url && failed !== url
      ? <img src={url} alt="" loading="lazy" className="size-full object-cover transition-transform duration-700 group-hover:scale-105"
          onError={() => setFailed(url)} />
      : <Shapes size={48} strokeWidth={1} className="mb-5 text-ink/40" />}
  </div>;
}
