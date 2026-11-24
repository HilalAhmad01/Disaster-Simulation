// One icon set for the drone pages: 24px grid, 1.5 stroke, drawn at 16px, colour from the text role (never their own colour).
const P = {
  back: '<path d="M15 6l-6 6 6 6"/>', pause: '<path d="M9 5v14M15 5v14"/>', play: '<path d="M7 4.5l12 7.5-12 7.5z"/>', plus: '<path d="M12 5v14M5 12h14"/>', minus: '<path d="M5 12h14"/>',
  pin: '<path d="M12 17v5M8 3h8l-1 7 3 3H6l3-3z"/>', skip: '<path d="M5 5l9 7-9 7zM19 5v14"/>', info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  up: '<path d="M6 15l6-6 6 6"/>', down: '<path d="M6 9l6 6 6-6"/>', close: '<path d="M6 6l12 12M18 6L6 18"/>', check: '<path d="M5 12.5l4.5 4.5L19 7.5"/>',
  drone: '<circle cx="5" cy="5" r="2.5"/><circle cx="19" cy="5" r="2.5"/><circle cx="5" cy="19" r="2.5"/><circle cx="19" cy="19" r="2.5"/><path d="M7 7l3.5 3.5M17 7l-3.5 3.5M7 17l3.5-3.5M17 17l-3.5-3.5"/><rect x="9.5" y="9.5" width="5" height="5" rx="1"/>',
  tablet: '<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M11 18h2"/>', download: '<path d="M12 4v11M7 11l5 5 5-5M5 20h14"/>', more: '<path d="M5 12h.01M12 12h.01M19 12h.01"/>',
  moon: '<path d="M20 14.5A8 8 0 0 1 9.5 4 8 8 0 1 0 20 14.5z"/>', sun: '<circle cx="12" cy="12" r="4"/><path d="M12 3v2M12 19v2M3 12h2M19 12h2M5.6 5.6L7 7M17 17l1.4 1.4M5.6 18.4L7 17M17 7l1.4-1.4"/>',
  target: '<circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/>', eye: '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/><circle cx="12" cy="12" r="3"/>',
  layers: '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>', home: '<path d="M4 11l8-7 8 7v9H4z"/>', hand: '<path d="M8 12V5.5a1.5 1.5 0 0 1 3 0V11m0-3.5a1.5 1.5 0 0 1 3 0V11m0-2.5a1.5 1.5 0 0 1 3 0V15a6 6 0 0 1-6 6h-1a5 5 0 0 1-4-2l-3-4.5a1.5 1.5 0 0 1 2.4-1.8L8 15"/>',
  route: '<circle cx="6" cy="18" r="2"/><circle cx="18" cy="6" r="2"/><path d="M8 18h6a3 3 0 0 0 0-6h-4a3 3 0 0 1 0-6h6"/>', send: '<path d="M5 12h14M13 6l6 6-6 6"/>', refresh: '<path d="M20 11a8 8 0 0 0-14-4L4 9M4 4v5h5M4 13a8 8 0 0 0 14 4l2-2M20 20v-5h-5"/>',
  record: '<circle cx="12" cy="12" r="4"/><circle cx="12" cy="12" r="9"/>', square: '<rect x="5" y="5" width="14" height="14" rx="2"/>', cube: '<path d="M21 8l-9-5-9 5v8l9 5 9-5z"/><path d="M3 8l9 5 9-5M12 13v8"/>',
};
export const icon = (n, cls = '') => `<svg class="i ${cls}" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${P[n] || ''}</svg>`;
