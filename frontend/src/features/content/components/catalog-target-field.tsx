"use client";

import { useEffect, useState } from "react";
import { Input, Select } from "@/components/ui/input";
import { publicCmsClient } from "../api";
import type { CmsList } from "../types";

type Target = { id: number; name: string };

export function CatalogTargetField({ id, kind, value, onChange }: {
  id: string; kind: "category" | "product"; value: string; onChange: (value: string) => void;
}) {
  const [search, setSearch] = useState("");
  const [options, setOptions] = useState<Target[]>([]);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      const resource = kind === "category" ? "categories" : "products";
      void publicCmsClient.get<CmsList<Target>>(`/api/products/${resource}/`, {
        params: { search, ...(kind === "category" ? { flat: true } : {}), page_size: 25 },
        signal: controller.signal,
      }).then(({ data }) => {
        if (!controller.signal.aborted) { setOptions(data.results); setFailed(false); }
      }).catch(() => {
        if (!controller.signal.aborted) { setOptions([]); setFailed(true); }
      });
    }, 250);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [kind, search]);
  return <>
    <Input aria-label={`Search ${kind} targets`} value={search} onChange={(e) => setSearch(e.target.value)} placeholder={`Search by ${kind} name`} />
    <Select id={id} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">General banner</option>
      {!!value && !options.some((option) => String(option.id) === value) && <option value={value}>Current {kind} #{value}</option>}
      {options.map((option) => <option key={option.id} value={option.id}>{option.name}</option>)}
    </Select>
    {failed && <p role="status" className="text-sm text-muted">Catalog targets are temporarily unavailable.</p>}
  </>;
}
