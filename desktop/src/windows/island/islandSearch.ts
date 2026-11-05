/** Search result card normalisation for the island. */
import type { KnowledgeCard } from "../../shared/core/state";

  export function cleanCard(raw: any): KnowledgeCard | undefined {
    if (!raw || typeof raw !== "object" || !String(raw.title || "").trim()) return undefined;
    const socials = (Array.isArray(raw.socials) ? raw.socials : [])
      .map((x: any) => ({ label: String(x?.label || "").trim(), url: String(x?.url || "").trim() }))
      .filter((x: { label: string; url: string }) => x.label && /^https?:\/\//i.test(x.url))
      .slice(0, 6);
    const watch = (Array.isArray(raw.watch) ? raw.watch : [])
      .map((x: any) => ({
        label: String(x?.label || "").trim(), url: String(x?.url || "").trim(),
        logo: /^https?:\/\//i.test(String(x?.logo || "")) ? String(x.logo) : undefined,
      }))
      .filter((x: { label: string; url: string }) => x.label && /^https?:\/\//i.test(x.url))
      .slice(0, 4);
    return {
      title: String(raw.title).trim(),
      subtitle: String(raw.subtitle || "").trim() || undefined,
      extract: String(raw.extract || "").trim() || undefined,
      image: /^https?:\/\//i.test(String(raw.image || "")) ? String(raw.image) : undefined,
      url: /^https?:\/\//i.test(String(raw.url || "")) ? String(raw.url) : undefined,
      urlLabel: String(raw.url_label || "").trim() || undefined,
      socials,
      watch: watch.length ? watch : undefined,
    };
  }
