/** SVG icons used by the island HUD / activities (extracted from island.ts). */

  export function volumeIconSvg(level: number, muted: boolean): string {
    const cone = '<path d="M3 9v6h4l5 4V5L7 9H3z" fill="currentColor"/>';
    const wave1 = '<path d="M15.5 8.5a5 5 0 0 1 0 7" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/>';
    const wave2 = '<path d="M18.2 5.8a9 9 0 0 1 0 12.4" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/>';
    const cross = '<path d="M16 9.5l5 5m0-5l-5 5" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round"/>';
    const inner = muted || level <= 0.001 ? cone + cross : level < 0.5 ? cone + wave1 : cone + wave1 + wave2;
    return `<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true">${inner}</svg>`;
  }



  export function activityIconSvg(kind: string, phase: string, app: string, title: string, iconUrl?: string, progress?: number): string {
    const brands: Record<string, string> = {
      discord: '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path fill="currentColor" d="M19.27 5.33C17.94 4.71 16.5 4.26 15 4a.09.09 0 0 0-.07.03c-.18.33-.39.76-.53 1.09a16.1 16.1 0 0 0-4.8 0c-.14-.34-.35-.76-.54-1.09c-.01-.02-.04-.03-.07-.03c-1.5.26-2.93.71-4.27 1.33c-.01 0-.02.01-.03.02c-2.72 4.07-3.47 8.03-3.1 11.95c0 .02.01.04.03.05c1.8 1.32 3.53 2.12 5.24 2.65c.03.01.06 0 .07-.02c.4-.55.76-1.13 1.07-1.74c.02-.04 0-.08-.04-.09c-.57-.22-1.11-.48-1.64-.78c-.04-.02-.04-.08-.01-.11c.11-.08.22-.17.33-.25c.02-.02.05-.02.07-.01c3.44 1.57 7.15 1.57 10.55 0c.02-.01.05-.01.07.01c.11.09.22.17.33.26c.04.03.03.09-.01.11c-.52.31-1.07.56-1.64.78c-.04.01-.05.06-.04.09c.32.61.68 1.19 1.07 1.74c.02.02.05.03.08.02c1.72-.53 3.45-1.33 5.25-2.65c.02-.01.03-.03.03-.05c.44-4.53-.73-8.46-3.1-11.95c-.01-.01-.02-.02-.04-.02zM8.52 14.91c-1.03 0-1.89-.95-1.89-2.12s.84-2.12 1.89-2.12c1.06 0 1.9.96 1.89 2.12c0 1.17-.84 2.12-1.89 2.12zm6.97 0c-1.03 0-1.89-.95-1.89-2.12s.84-2.12 1.89-2.12c1.06 0 1.9.96 1.89 2.12c0 1.17-.83 2.12-1.89 2.12z"/></svg>',
      spotify: '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path fill="currentColor" d="M12 1.5C6.2 1.5 1.5 6.2 1.5 12S6.2 22.5 12 22.5 22.5 17.8 22.5 12 17.8 1.5 12 1.5zm4.86 15.12a.66.66 0 0 1-.9.22c-2.47-1.51-5.58-1.85-9.24-1.01a.66.66 0 1 1-.29-1.28c4.01-.91 7.42-.52 10.21 1.17a.66.66 0 0 1 .22.9zm1.3-2.89a.82.82 0 0 1-1.13.27c-2.83-1.74-7.14-2.24-10.48-1.23a.82.82 0 1 1-.48-1.57c3.8-1.16 8.54-.6 11.82 1.42a.82.82 0 0 1 .27 1.11zm.11-3.01c-3.39-2.01-8.98-2.2-12.21-1.21a.99.99 0 1 1-.57-1.89c3.7-1.13 9.86-.91 13.74 1.4a.99.99 0 0 1-.96 1.7z"/></svg>',
      chrome: '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><circle cx="12" cy="12" r="3.2" fill="none" stroke="currentColor" stroke-width="1.8"/><path fill="none" stroke="currentColor" stroke-width="1.8" d="M12 5.2h7.1A8.8 8.8 0 0 0 12 3.2a8.8 8.8 0 0 0-7.1 3.5L8.4 12M12 5.2L8.4 12h7.2L12 5.2zm3.6 6.8l3.5 6.1A8.8 8.8 0 0 0 20.8 12 8.8 8.8 0 0 0 19.1 7H12"/></svg>',
      code: '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M8 7l-4 5 4 5M16 7l4 5-4 5M14 4l-4 16"/></svg>',
      vscode: '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M8 7l-4 5 4 5M16 7l4 5-4 5M14 4l-4 16"/></svg>',
      "vs code": '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" d="M8 7l-4 5 4 5M16 7l4 5-4 5M14 4l-4 16"/></svg>',
      steam: '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="1.8"/><circle cx="12" cy="12" r="3" fill="currentColor"/></svg>',
    };
    if (kind === "download") {
      if (phase === "success") {
        return '<svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true"><path d="M12 3v11m0 0l-4-4m4 4l4-4M5 19h14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
      }
      if (phase === "fail") {
        return '<svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="M9 9l6 6M15 9l-6 6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>';
      }
      const pct = Number.isFinite(progress) ? Math.max(0, Math.min(100, Number(progress))) : null;
      if (pct != null) {
        const circumference = 2 * Math.PI * 9;
        const offset = circumference * (1 - pct / 100);
        return `<svg class="download-progress-icon" viewBox="0 0 24 24" width="24" height="24" aria-hidden="true"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-opacity=".20" stroke-width="2.4"/><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-dasharray="${circumference.toFixed(2)}" stroke-dashoffset="${offset.toFixed(2)}" transform="rotate(-90 12 12)"/><path d="M12 7v8m0 0l-3-3m3 3l3-3" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
      }
      return '<svg class="download-progress-icon indeterminate" viewBox="0 0 24 24" width="24" height="24" aria-hidden="true"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-opacity=".18" stroke-width="2.4"/><circle class="download-spinner-ring" cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-dasharray="18 39" transform="rotate(-90 12 12)"/><path d="M12 7v8m0 0l-3-3m3 3l3-3" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    }
    if (kind === "app_launch") {
      if (iconUrl && iconUrl.startsWith("data:image/")) {
        return `<img class="activity-app-icon" src="${iconUrl}" alt="" />`;
      }
      if (app && brands[app]) return brands[app];
      // Letter tile
      const letter = ((app || title).replace(/^opening\s+/i, "").replace(/\u2026|\.\.\.|opened|couldn.*/i, "").trim() || "?").charAt(0).toUpperCase();
      return `<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><rect x="2" y="2" width="20" height="20" rx="5" fill="none" stroke="currentColor" stroke-width="1.8"/><text x="12" y="16" text-anchor="middle" font-size="11" font-weight="700" fill="currentColor" font-family="system-ui,sans-serif">${letter}</text></svg>`;
    }
    if (phase === "success") {
      return '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="M8 12.5l2.5 2.5 5-5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    }
    if (phase === "fail") {
      return '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="M9 9l6 6M15 9l-6 6" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/></svg>';
    }
    return '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><circle cx="12" cy="12" r="8" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 8v4l2.5 2.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>';
  }



  export function spotifyIconSvg(size = 18): string {
    return `<svg viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true"><path fill="currentColor" d="M12 1.5C6.2 1.5 1.5 6.2 1.5 12S6.2 22.5 12 22.5 22.5 17.8 22.5 12 17.8 1.5 12 1.5zm4.86 15.12a.66.66 0 0 1-.9.22c-2.47-1.51-5.58-1.85-9.24-1.01a.66.66 0 1 1-.29-1.28c4.01-.91 7.42-.52 10.21 1.17a.66.66 0 0 1 .22.9zm1.3-2.89a.82.82 0 0 1-1.13.27c-2.83-1.74-7.14-2.24-10.48-1.23a.82.82 0 1 1-.48-1.57c3.8-1.16 8.54-.6 11.82 1.42a.82.82 0 0 1 .27 1.11zm.11-3.01c-3.39-2.01-8.98-2.2-12.21-1.21a.99.99 0 1 1-.57-1.89c3.7-1.13 9.86-.91 13.74 1.4a.99.99 0 0 1-.96 1.7z"/></svg>`;
  }



  export function batteryIconSvg(pct: number, charging: boolean): string {
    const w = Math.max(1, Math.round((pct / 100) * 13));
    const bolt = charging ? '<path d="M12.5 6.5l-3 4h2.2l-1.2 3.5 3.5-4.5h-2.2z" fill="#000"/>' : "";
    return `<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><rect x="2" y="7" width="17" height="10" rx="2.2" fill="none" stroke="currentColor" stroke-width="2"/><rect x="20" y="10.5" width="2" height="3" rx="1" fill="currentColor"/><rect x="4" y="9" width="${w}" height="6" rx="1" fill="currentColor"/>${bolt}</svg>`;
  }


