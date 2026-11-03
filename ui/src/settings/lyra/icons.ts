// Line icons for the settings sidebar (24×24, stroke = currentColor), in the same quiet style as Lyra's.
// One entry per tab id; anything unknown falls back to a ring.

const S = 'fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"';

const P: Record<string, string> = {
  general: '<circle cx="12" cy="12" r="3.6"/><path d="M12 3v2.3M12 18.7V21M3 12h2.3M18.7 12H21M5.6 5.6l1.6 1.6M16.8 16.8l1.6 1.6M18.4 5.6l-1.6 1.6M7.2 16.8l-1.6 1.6"/>',
  ai: '<path d="M10.5 4l1.7 4.8 4.8 1.7-4.8 1.7-1.7 4.8-1.7-4.8L4 10.5l4.8-1.7z"/><path d="M18.5 3.5l.7 1.8 1.8.7-1.8.7-.7 1.8-.7-1.8-1.8-.7 1.8-.7zM18 15.5l.6 1.5 1.5.6-1.5.6-.6 1.5-.6-1.5-1.5-.6 1.5-.6z"/>',
  voice: '<path d="M4 10v4M8 6.5v11M12 3.5v17M16 7.5v9M20 10.5v3"/>',
  p2_dictation: '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5.5 11.2a6.5 6.5 0 0 0 13 0M12 17.7V21M9 21h6"/>',
  notetaker: '<circle cx="12" cy="12" r="8.6"/><circle cx="12" cy="12" r="3.2"/>',
  shortcuts: '<rect x="2.8" y="6" width="18.4" height="12" rx="2.6"/><path d="M6.8 10h.01M10.3 10h.01M13.8 10h.01M17.3 10h.01M7.5 14h9"/>',
  statistics: '<path d="M5 20v-8M12 20V4M19 20v-5"/>',
  island: '<rect x="2.8" y="4.5" width="18.4" height="15" rx="3.4"/><rect x="8" y="7.6" width="8" height="2.6" rx="1.3"/>',
  mochi: '<path d="M4.6 13.4C4.6 9.6 7.9 7 12 7s7.4 2.6 7.4 6.4c0 3-2.6 5.1-7.4 5.1s-7.4-2.1-7.4-5.1z"/><path d="M9.4 12.6v1.4M14.6 12.6v1.4"/>',
  features: '<rect x="3.6" y="3.6" width="7" height="7" rx="2"/><rect x="13.4" y="3.6" width="7" height="7" rx="2"/><rect x="3.6" y="13.4" width="7" height="7" rx="2"/><rect x="13.4" y="13.4" width="7" height="7" rx="2"/>',
  apps: '<path d="M4 5.8A2.8 2.8 0 0 1 6.8 3h10.4A2.8 2.8 0 0 1 20 5.8v7.4a2.8 2.8 0 0 1-2.8 2.8H11l-4.4 4v-4A2.8 2.8 0 0 1 4 13.2z"/>',
  live: '<circle cx="12" cy="12" r="2"/><path d="M7.8 7.8a6 6 0 0 0 0 8.4M16.2 7.8a6 6 0 0 1 0 8.4M5 5a10 10 0 0 0 0 14M19 5a10 10 0 0 1 0 14"/>',
  screen: '<rect x="2.8" y="4" width="18.4" height="12.4" rx="2.4"/><path d="M8.5 20.2h7M12 16.6v3.6"/>',
  privacy: '<rect x="4.6" y="10.6" width="14.8" height="10" rx="2.6"/><path d="M8 10.6V8a4 4 0 0 1 8 0v2.6"/>',
  files: '<path d="M3.4 6.6A2.6 2.6 0 0 1 6 4h3.6l2 2.2H18A2.6 2.6 0 0 1 20.6 8.8v8.6A2.6 2.6 0 0 1 18 20H6a2.6 2.6 0 0 1-2.6-2.6z"/>',
  advanced: '<path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/>',
  about: '<circle cx="12" cy="12" r="8.8"/><path d="M12 11v5.2M12 7.7h.01"/>',
};

export function lineIcon(id: string, size = 19): string {
  const body = P[id] ?? '<circle cx="12" cy="12" r="7.5"/>';
  return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" ${S} aria-hidden="true">${body}</svg>`;
}
