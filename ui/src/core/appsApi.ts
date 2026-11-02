// What the backend says about app plugins (WhatsApp): shared by the island's Apps tab and by
// Settings → Apps, where an app is set up. See backend/plugins/apps.py for the data these mirror.

export const APPS_BASE = "http://127.0.0.1:8765";

export interface AppField {
  key: string; label: string; hint: string; secret: boolean; type: "text" | "toggle"; set: boolean; value: string | boolean;
}
export interface AppInfo {
  id: string; title: string; icon: string; accent: string; description: string;
  connected: boolean; account: string; message: string; canConnect: boolean; settings: AppField[];
}
export interface AppAction { name: string; params: Record<string, unknown> }
export interface AppItem { id: string; title: string; subtitle: string; time: string; image: string; badge: string; action?: AppAction }
/** A photo or voice message inside a thread message (backend/plugins/apps.py `_clean_media`). */
export interface AppMedia {
  kind: "voice" | "audio" | "image";
  state: "loading" | "ready" | "failed" | "off" | "viewonce" | "expired";
  url: string; mime: string; seconds: number; w: number; h: number;
  transcript: string; tstate: "" | "pending" | "done" | "none" | "failed" | "off"; note: string;
}
export interface AppMessage { id: string; from: string; text: string; time: string; mine: boolean; media?: AppMedia }
export interface AppView {
  type: "list" | "thread" | "message" | "qr"; title: string; route: string; text?: string; image?: string; chip?: string;
  items?: AppItem[]; empty?: string; actions?: Array<AppAction & { label: string }>;
  messages?: AppMessage[];
  compose?: { action: string; params: Record<string, unknown>; placeholder: string; draft?: string; suggest?: AppAction; attach?: boolean };
  /** A short line about the open chat, e.g. "Not sent: too large". */
  notice?: string;
}

export async function appsPost<T>(path: string, body: object): Promise<T | null> {
  try {
    const res = await fetch(`${APPS_BASE}${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

/** Every app plugin with its sign-in state; null when the backend does not answer. */
export async function fetchApps(): Promise<{ enabled: boolean; apps: AppInfo[] } | null> {
  try {
    const res = await fetch(`${APPS_BASE}/apps`);
    const data = (await res.json()) as { enabled: boolean; apps: AppInfo[] };
    return { enabled: data.enabled !== false, apps: data.apps ?? [] };
  } catch {
    return null;
  }
}

/** The island asks Settings to open on the Apps section (the two windows share localStorage). */
export const SETTINGS_GOTO_KEY = "lyra.settings.goto";

// ── Photos and voice messages ────────────────────────────────────────────────────────────────────────────
// The app's CSP only allows https: images and blob: media, so a saved file is fetched from the local backend
// (connect-src allows it) and shown from a blob: URL. Kept in a small cache so a refresh does not refetch it.

const blobCache = new Map<string, Promise<string | null>>();
const blobDone = new Map<string, string>();       // the ones already fetched, readable without waiting
const BLOB_CACHE_MAX = 60;

/** The blob: URL of a media file if it is already fetched (so a repaint can draw it at once), else "". */
export const peekMediaUrl = (path: string): string => blobDone.get(path) ?? "";

export function mediaBlobUrl(path: string): Promise<string | null> {
  const hit = blobCache.get(path);
  if (hit) return hit;
  const job = (async () => {
    try {
      const res = await fetch(`${APPS_BASE}${path}`);
      if (!res.ok) return null;
      const url = URL.createObjectURL(await res.blob());
      blobDone.set(path, url);
      return url;
    } catch {
      return null;
    }
  })();
  blobCache.set(path, job);
  if (blobCache.size > BLOB_CACHE_MAX) {
    const oldest = blobCache.keys().next().value as string;
    blobCache.delete(oldest);
    const url = blobDone.get(oldest);
    blobDone.delete(oldest);
    if (url) URL.revokeObjectURL(url);
  }
  void job.then((u) => { if (!u) blobCache.delete(path); });      // a failed fetch may be tried again
  return job;
}

// ── A file picked with the + button ──────────────────────────────────────────────────────────────────────
export const UPLOAD_MAX_BYTES = 16 * 1024 * 1024;
export interface AppUpload { ok: boolean; token?: string; name?: string; kind?: "image" | "file"; size?: number; error?: string }

/** Hands the chosen file to the app, which keeps it until the user presses Send. Nothing is sent by this call. */
export async function appsUpload(appId: string, file: File): Promise<AppUpload> {
  if (file.size > UPLOAD_MAX_BYTES) return { ok: false, error: "That file is larger than 16 MB." };
  try {
    const r = await fetch(`${APPS_BASE}/apps/upload?id=${encodeURIComponent(appId)}&name=${encodeURIComponent(file.name)}`, {
      method: "POST", headers: { "Content-Type": "application/octet-stream" }, body: file,
    });
    return (await r.json()) as AppUpload;
  } catch {
    return { ok: false, error: "Lyra is unavailable." };
  }
}
