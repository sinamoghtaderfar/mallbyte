"use client";

import { useState } from "react";

import { safeImageUrl } from "@/features/content/safety";
import { getImageUrl } from "@/lib/media/image-url";

function ThumbnailImage({ sources, name }: { sources: string[]; name: string }) {
  const [index, setIndex] = useState(0);
  const src = sources[index];
  if (!src) return <span aria-hidden="true">{name.slice(0, 1)}</span>;

  // The API storage URL already identifies the appropriate public media file.
  // eslint-disable-next-line @next/next/no-img-element
  return <img key={src} src={src} alt={name} loading="lazy" className="size-full object-contain"
    onError={() => setIndex(index + 1)} />;
}

export function ProductThumbnail({ image, fallbackImages, name }: {
  image?: string | null;
  fallbackImages?: string[];
  name: string;
}) {
  const sources = [...new Set([image, ...(fallbackImages ?? [])].flatMap((path) => {
    const url = getImageUrl(safeImageUrl(path));
    return url ? [url] : [];
  }))];
  return <ThumbnailImage key={JSON.stringify(sources)} sources={sources} name={name} />;
}
