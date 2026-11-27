"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { apiClient } from "@/lib/api/client";
import type { ProductCategory } from "../types";
import { CategoryArtwork } from "./category-artwork";

export function CategoryIntroduction({ target }: { target?: string }) {
  const [category, setCategory] = useState<ProductCategory>();
  useEffect(() => {
    if (!target || !/^[1-9]\d*$/.test(target)) return;
    let current = true;
    void apiClient.get<ProductCategory>(`/api/products/categories/${target}/`)
      .then(({ data }) => { if (current) setCategory(data); })
      .catch(() => { if (current) setCategory(undefined); });
    return () => { current = false; };
  }, [target]);
  if (!category || String(category.id) !== target) return null;
  return <section aria-label="Selected category" className="mb-6 flex items-center gap-4 rounded-2xl border border-line p-3">
    <div className="relative isolate aspect-[5/4] w-20 shrink-0 overflow-hidden rounded-xl sm:w-28">
      <CategoryArtwork image={category.image} />
    </div>
    <div className="min-w-0">
      <h2 className="mb-display break-words text-2xl">{category.name}</h2>
      {!!category.children?.length && <div className="flex flex-wrap gap-x-3 gap-y-1 text-sm text-muted">
        {category.children.map((child) => <Link key={child.id} href={`/products?category=${child.id}`} className="underline-offset-4 hover:underline">{child.name}</Link>)}
      </div>}
    </div>
  </section>;
}
