import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { SiteShell } from "@/components/layout/site-shell";
import { PublicContentPage } from "@/features/content/components/public-content-page";
import type { ContentPage } from "@/features/content/types";
import { getPublicApiBaseUrl } from "@/lib/api/origin";

type Props = { params: Promise<{ slug: string }> };
async function getPage(slug: string): Promise<ContentPage | null> {
  const root = process.env.INTERNAL_API_BASE_URL?.replace(/\/$/, "") || getPublicApiBaseUrl();
  const response = await fetch(`${root}/api/content/pages/${encodeURIComponent(slug)}/`, { cache: "no-store", signal: AbortSignal.timeout(12000) });
  if (response.status === 404) return null;
  if (!response.ok) throw new Error("CMS page service unavailable");
  const page = await response.json() as ContentPage;
  return page.status === "published" && page.is_visible ? page : null;
}
export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug } = await params;
  const page = await getPage(slug);
  if (!page) return { title: "Page not found | MallByte", robots: { index: false } };
  return { title: `${page.meta_title || page.title} | MallByte`, description: page.meta_description || page.excerpt || undefined, keywords: page.meta_keywords || undefined };
}
export default async function CmsPage({ params }: Props) {
  const { slug } = await params;
  const page = await getPage(slug);
  if (!page) notFound();
  return <SiteShell><PublicContentPage page={page}/></SiteShell>;
}
