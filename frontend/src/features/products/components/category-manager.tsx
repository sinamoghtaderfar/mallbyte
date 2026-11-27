"use client";

/* eslint-disable @next/next/no-img-element */
import { useEffect, useState, type FormEvent } from "react";
import { AccessibleDialog } from "@/components/ui/accessible-dialog";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { Notice } from "@/components/ui/states";
import { apiClient } from "@/lib/api/client";
import { getApiErrorMessage } from "@/lib/api/errors";
import { mediaImage } from "@/features/content/components/storefront-banners";
import type { PaginatedResponse } from "@/types/api";
import type { ProductCategory } from "../types";

const endpoint = "/api/products/categories/";

export function CategoryManager() {
  const [result, setResult] = useState<PaginatedResponse<ProductCategory>>();
  const [edit, setEdit] = useState<ProductCategory | "new" | null>(null);
  const [error, setError] = useState("");
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    void apiClient.get<PaginatedResponse<ProductCategory>>(`${endpoint}manage/`, {
      params: { page, search: query }, signal: controller.signal,
    }).then(({ data }) => {
      if (!controller.signal.aborted) { setResult(data); setError(""); }
    }).catch((reason) => {
      if (!controller.signal.aborted) { setError(getApiErrorMessage(reason)); setResult(undefined); }
    });
    return () => controller.abort();
  }, [page, query, revision]);
  return <main className="mb-container py-10">
    <h1 className="mb-display text-4xl">Product categories</h1>
    <div className="mt-5 flex flex-wrap justify-between gap-3">
      <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); setPage(1); setQuery(search); }}>
        <Input aria-label="Find categories" value={search} onChange={(e) => setSearch(e.target.value)} />
        <Button type="submit" variant="secondary">Search</Button>
      </form>
      {result && <Button onClick={() => setEdit("new")}>Add category</Button>}
    </div>
    {error && <Notice tone="danger" className="mt-4">{error}</Notice>}
    {result && <ul className="mt-5 divide-y divide-line rounded-xl border border-line">
      {result.results.length === 0 && <li className="p-4">No categories found.</li>}
      {result.results.map((category) => <li key={category.id} className="flex items-center justify-between gap-3 p-4">
        <span>{category.name} <small className="text-muted">#{category.id}{category.parent_name ? ` · ${category.parent_name}` : ""}</small></span>
        <Button variant="secondary" size="small" onClick={() => setEdit(category)} aria-label={`Edit ${category.name}`}>Edit</Button>
      </li>)}
    </ul>}
    {result && <nav aria-label="Category pages" className="mt-3 flex gap-2">
      <Button variant="secondary" disabled={!result.previous} onClick={() => setPage(page - 1)}>Previous</Button>
      <Button variant="secondary" disabled={!result.next} onClick={() => setPage(page + 1)}>Next</Button>
    </nav>}
    {edit && <CategoryEditor initial={edit === "new" ? undefined : edit} onClose={() => setEdit(null)}
      onSave={() => { setEdit(null); setRevision((value) => value + 1); }} />}
  </main>;
}

export function CategoryEditor({ initial, onClose, onSave }: {
  initial?: ProductCategory; onClose: () => void; onSave: () => void;
}) {
  const [upload, setUpload] = useState<{ file: File; preview: string }>();
  const [remove, setRemove] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => () => {
    if (upload) URL.revokeObjectURL(upload.preview);
  }, [upload]);
  const currentImage = remove ? null : upload?.preview || mediaImage(initial?.image);
  async function save(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const fields = new FormData(e.currentTarget);
    fields.set("is_active", String(fields.has("is_active")));
    if (upload) fields.set("image", upload.file);
    else if (remove) fields.set("image", "");
    setBusy(true);
    setError("");
    try {
      const config = { headers: { "Content-Type": undefined }, timeout: 60000 };
      if (initial) await apiClient.patch(`${endpoint}${initial.id}/`, fields, config);
      else await apiClient.post(endpoint, fields, config);
      onSave();
    } catch (reason) {
      setError(getApiErrorMessage(reason));
    } finally { setBusy(false); }
  }
  return <AccessibleDialog onClose={onClose} dismissible={!busy} labelledBy="category-editor-heading"
    overlayClassName="fixed inset-0 z-[60] grid place-items-center overflow-auto bg-ink/45 p-4" className="w-full max-w-xl rounded-2xl bg-surface p-5">
    <h2 id="category-editor-heading" className="mb-display text-2xl">{initial ? "Edit category" : "Add category"}</h2>
    <form onSubmit={(e) => void save(e)} className="mt-4 grid gap-3">
      <label className="grid gap-1">Name<Input required name="name" maxLength={100} defaultValue={initial?.name} /></label>
      <label className="grid gap-1">Description<Textarea name="description" rows={2} defaultValue={initial?.description} /></label>
      <div className="grid grid-cols-2 gap-3">
        <label className="grid gap-1">Parent category ID<Input name="parent" type="number" min={1} defaultValue={initial?.parent ?? ""} /></label>
        <label className="grid gap-1">Display order<Input name="order" type="number" defaultValue={initial?.order ?? 0} /></label>
      </div>
      <label className="flex items-center gap-2"><input type="checkbox" name="is_active" defaultChecked={initial?.is_active ?? true} /> Active</label>
      <label className="grid gap-1">Category image
        <input type="file" accept="image/jpeg,image/png,image/webp" className="max-w-full text-sm" onChange={(e) => {
          const file = e.target.files?.[0];
          if (!file) return;
          if (file.size > 8 * 1024 * 1024) { setError("Choose an image no larger than 8 MB."); e.target.value = ""; return; }
          setUpload({ file, preview: URL.createObjectURL(file) });
          setRemove(false);
          setError("");
        }} />
      </label>
      {currentImage && <div className="flex items-center gap-3">
        <img src={currentImage} alt="Category preview" className="aspect-[5/4] w-24 rounded-lg object-cover" />
        <Button size="small" variant="secondary" onClick={() => { setRemove(true); setUpload(undefined); }}>Remove image</Button>
      </div>}
      {error && <Notice tone="danger">{error}</Notice>}
      <div className="flex justify-end gap-3">
        <Button variant="secondary" disabled={busy} onClick={onClose}>Cancel</Button>
        <Button type="submit" loading={busy}>Save category</Button>
      </div>
    </form>
  </AccessibleDialog>;
}
