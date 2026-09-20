// icons.js — MeshWX web icon set.
//
// One 24x24 line-icon language for the whole web client: 2px padding, 1.75
// stroke, round caps and joins, colour always `currentColor` so CSS tints.
// Every glyph is inline SVG built here — no icon font, no network, no npm,
// because the app has to work with the radio and no internet.
//
// Names follow the SF Symbol names the iOS weather tool uses, so a screen
// ported from MC1/Views/Tools/Weather can keep its icon name verbatim.
//
// Drawings are original: derived from circles, arcs and short lines on the
// grid below, not copied from any icon library.

const r2 = (n) => Math.round(n * 100) / 100;

/**
 * The one cloud outline every cloud glyph reuses, offset by (dx, dy).
 * Untranslated it spans x 4.9..19.4, y 8.25..18 with a flat base at y=18:
 * a left bump (r 3.3), a wide top (r 4.6) and a right bump (r 3.6).
 */
const cloud = (dx = 0, dy = 0) => {
  const x = (n) => r2(n + dx);
  const y = (n) => r2(n + dy);
  return `<path d="M${x(8.2)} ${y(18)}A3.3 3.3 0 0 1 ${x(8)} ${y(11.6)}` +
    `A4.6 4.6 0 0 1 ${x(16.6)} ${y(10.9)}A3.6 3.6 0 0 1 ${x(16.2)} ${y(18)}Z"/>`;
};

/** Sun rays: `count` evenly spaced spokes from radius `inner` to `outer`. */
const rays = (cx, cy, inner, outer, angles) =>
  angles.map((deg) => {
    const t = (deg * Math.PI) / 180;
    const c = Math.cos(t);
    const s = Math.sin(t);
    return `<line x1="${r2(cx + c * inner)}" y1="${r2(cy + s * inner)}" ` +
      `x2="${r2(cx + c * outer)}" y2="${r2(cy + s * outer)}" ` +
      `stroke="currentColor" fill="none"/>`;
  }).join('');

const ALL = [0, 45, 90, 135, 180, 225, 270, 315];
const UPPER = [225, 270, 315, 0, 180];

/** A slanted rain stroke, top-right to bottom-left. */
const slash = (x, y, len = 3.4, lean = 1.2) =>
  `<line x1="${r2(x)}" y1="${r2(y)}" x2="${r2(x - lean)}" y2="${r2(y + len)}"/>`;

/** A three-line snow asterisk: the six-armed star behind `snowflake`. */
const flake = (cx, cy, r) => {
  const a = r2(r * 0.866);
  const b = r2(r * 0.5);
  return `<line x1="${r2(cx)}" y1="${r2(cy - r)}" x2="${r2(cx)}" y2="${r2(cy + r)}"/>` +
    `<line x1="${r2(cx - a)}" y1="${r2(cy - b)}" x2="${r2(cx + a)}" y2="${r2(cy + b)}"/>` +
    `<line x1="${r2(cx - a)}" y1="${r2(cy + b)}" x2="${r2(cx + a)}" y2="${r2(cy - b)}"/>`;
};

/** A horizontal S-wave: `segs` full waves of width `w`, amplitude `amp`. */
const wave = (x0, y, w, amp, segs) => {
  let d = `M${r2(x0)} ${r2(y)}`;
  for (let i = 0; i < segs; i += 1) {
    const a = x0 + i * w;
    d += `C${r2(a + w / 3)} ${r2(y - amp)} ${r2(a + (2 * w) / 3)} ${r2(y + amp)} ${r2(a + w)} ${r2(y)}`;
  }
  return `<path d="${d}"/>`;
};

/** A filled dot — stays filled in both the stroked and the filled rendering. */
const dot = (cx, cy, r) =>
  `<circle cx="${cx}" cy="${cy}" r="${r}" fill="currentColor" stroke="none"/>`;

/**
 * The drawings. Keys are SF Symbol names; `paths` is the inner markup of a
 * 24x24 viewBox. `filled: true` means the glyph is drawn with fills rather
 * than strokes (see `icon()`).
 */
export const ICONS = {
  // ---------------------------------------------------------------- weather

  'sun.max': {
    paths: `<circle cx="12" cy="12" r="3.9"/>${rays(12, 12, 6, 8, ALL)}`,
  },

  'moon.stars': {
    paths:
      '<path d="M16.18 13.91A5.6 5.6 0 1 1 8.82 8.09A5.6 5.6 0 0 0 16.18 13.91Z"/>' +
      '<path d="M19 4.2L19.72 5.88 21.4 6.6 19.72 7.32 19 9 18.28 7.32 16.6 6.6 18.28 5.88Z" fill="currentColor" stroke="none"/>' +
      '<path d="M19.8 11L20.34 12.26 21.6 12.8 20.34 13.34 19.8 14.6 19.26 13.34 18 12.8 19.26 12.26Z" fill="currentColor" stroke="none"/>',
  },

  cloud: { paths: cloud() },

  'cloud.sun': {
    paths:
      '<circle cx="17" cy="7" r="2.5"/>' +
      rays(17, 7, 3.5, 4.9, [225, 270, 315, 0, 45]) +
      cloud(-0.8, 1.4),
  },

  'cloud.moon': {
    paths:
      '<path d="M20.32 7.72A3.4 3.4 0 1 1 16.28 3.68A3.4 3.4 0 0 0 20.32 7.72Z"/>' +
      cloud(-0.8, 1.4),
  },

  'cloud.fog': {
    paths:
      cloud(0, -3) +
      '<line x1="6.4" y1="18.2" x2="17.6" y2="18.2"/>' +
      '<line x1="8.6" y1="21.2" x2="15.4" y2="21.2"/>',
  },

  'cloud.rain': {
    paths: cloud(0, -3.2) + slash(9.6, 17) + slash(12.6, 17) + slash(15.6, 17),
  },

  'cloud.drizzle': {
    paths:
      cloud(0, -3.2) +
      slash(9.6, 17.2, 1.8, 0.6) +
      slash(12.6, 17.2, 1.8, 0.6) +
      slash(15.6, 17.2, 1.8, 0.6) +
      slash(11.1, 20, 1.8, 0.6) +
      slash(14.1, 20, 1.8, 0.6),
  },

  'cloud.snow': {
    paths: cloud(0, -3.2) + dot(9.4, 18.2, 1.25) + dot(14.6, 18.2, 1.25) + dot(12, 21.4, 1.25),
  },

  'cloud.sleet': {
    paths: cloud(0, -3.2) + slash(9.6, 17.2, 3.2, 1.1) + dot(13.2, 18.6, 1.25) + slash(17.2, 17.2, 3.2, 1.1),
  },

  'cloud.bolt': {
    paths: cloud(0, -3.2) + '<polyline points="13.8 16.4 10.8 19.9 13.1 19.9 11.2 22"/>',
  },

  'cloud.bolt.rain': {
    paths:
      cloud(0, -3.2) +
      slash(8.4, 17, 3, 1) +
      '<polyline points="14.2 16.6 11.6 19.7 13.6 19.7 12 22"/>' +
      slash(17.6, 17, 3, 1),
  },

  smoke: {
    paths: wave(3.2, 7, 4.6, 3, 3) + wave(5.4, 12.4, 4.6, 3, 3) + wave(4.2, 17.8, 4.6, 3, 2),
  },

  'sun.haze': {
    paths:
      '<circle cx="12" cy="9.2" r="3"/>' +
      rays(12, 9.2, 4, 5.4, UPPER) +
      '<line x1="4.4" y1="16.4" x2="19.6" y2="16.4"/>' +
      '<line x1="7" y1="19.8" x2="17" y2="19.8"/>',
  },

  'moon.haze': {
    paths:
      '<path d="M15.89 10.02A4.6 4.6 0 1 1 10.11 4.58A4.6 4.6 0 0 0 15.89 10.02Z"/>' +
      '<line x1="4.6" y1="17.2" x2="19.4" y2="17.2"/>' +
      '<line x1="7.2" y1="20.4" x2="16.8" y2="20.4"/>',
  },

  'sun.dust': {
    paths:
      '<circle cx="12" cy="9.4" r="3.2"/>' +
      rays(12, 9.4, 4.2, 5.6, UPPER) +
      '<line x1="5.4" y1="16.8" x2="10.2" y2="16.8"/>' +
      '<line x1="12.6" y1="16.8" x2="18.6" y2="16.8"/>' +
      '<line x1="7.4" y1="20" x2="12.2" y2="20"/>' +
      '<line x1="14.6" y1="20" x2="19.4" y2="20"/>',
  },

  wind: {
    paths:
      '<path d="M3.4 8.4H13.2A2.6 2.6 0 1 0 10.6 5.8"/>' +
      '<path d="M3.4 13.2H18A2.8 2.8 0 1 1 15.2 16"/>' +
      '<line x1="3.4" y1="18.2" x2="11.4" y2="18.2"/>',
  },

  tornado: {
    paths:
      '<line x1="3.6" y1="5" x2="20.4" y2="5"/>' +
      '<line x1="5" y1="8.2" x2="19" y2="8.2"/>' +
      '<line x1="6.6" y1="11.4" x2="17.4" y2="11.4"/>' +
      '<line x1="8.4" y1="14.6" x2="15.6" y2="14.6"/>' +
      '<line x1="10.2" y1="17.8" x2="13.8" y2="17.8"/>' +
      '<path d="M12.6 17.8C12 19.6 11.4 20.6 10.4 21.6"/>',
  },

  'water.waves': {
    paths: wave(3, 9, 6, 1.7, 3) + wave(3, 14, 6, 1.7, 3) + wave(3, 19, 6, 1.7, 3),
  },

  drop: {
    paths: '<path d="M12 3.4C12 3.4 5.8 10.2 5.8 15A6.2 6.2 0 0 0 18.2 15C18.2 10.2 12 3.4 12 3.4Z"/>',
  },

  snowflake: {
    paths:
      flake(12, 12, 8) +
      '<polyline points="9.6 6.4 12 4 14.4 6.4"/>' +
      '<polyline points="9.6 17.6 12 20 14.4 17.6"/>',
  },

  flame: {
    paths:
      '<path d="M12.6 2.6C13 6.4 16.8 8.6 16.8 13.4A4.8 4.8 0 0 1 7.2 13.4C7.2 10.6 9 9.4 9.8 7.8C10.6 6.2 10.2 4.6 10.2 4.6C10.2 4.6 12.2 5.4 12.6 2.6Z"/>' +
      '<path d="M12 18.4C10.2 18.4 9 17 9 15.4C9 13 11.8 10.8 11.8 10.8C11.8 10.8 15 13 15 15.4C15 17 13.8 18.4 12 18.4Z"/>',
  },

  'thermometer.medium': {
    paths:
      '<path d="M14.2 13.6V6.2A2.2 2.2 0 0 0 9.8 6.2V13.6A4.4 4.4 0 1 0 14.2 13.6Z"/>' +
      dot(12, 17.4, 2) +
      '<line x1="15.6" y1="7.6" x2="18" y2="7.6"/>' +
      '<line x1="15.6" y1="10.6" x2="18" y2="10.6"/>',
  },

  'thermometer.sun': {
    paths:
      '<path d="M8.8 12.4V5.6A1.6 1.6 0 0 0 5.6 5.6V12.4A3.2 3.2 0 1 0 8.8 12.4Z"/>' +
      dot(7.2, 15.2, 1.5) +
      '<circle cx="17.2" cy="9.6" r="2.3"/>' +
      rays(17.2, 9.6, 3.2, 4.4, ALL),
  },

  // ----------------------------------------------------------- navigation UI

  'chevron.left': { paths: '<polyline points="14.5 5.5 8 12 14.5 18.5"/>' },
  'chevron.right': { paths: '<polyline points="9.5 5.5 16 12 9.5 18.5"/>' },
  'chevron.down': { paths: '<polyline points="5.5 9.5 12 16 18.5 9.5"/>' },

  xmark: {
    paths: '<line x1="6" y1="6" x2="18" y2="18"/><line x1="18" y1="6" x2="6" y2="18"/>',
  },

  checkmark: { paths: '<polyline points="4.8 12.6 9.6 17.4 19.2 6.6"/>' },

  'checkmark.circle': {
    paths: '<circle cx="12" cy="12" r="8.6"/><polyline points="7.8 12.2 10.8 15.2 16.2 8.8"/>',
  },

  // Filled variants with interior detail are drawn as one even-odd path so the
  // mark reads as a hole punched through the solid shape.
  'checkmark.circle.fill': {
    filled: true,
    paths:
      '<path fill-rule="evenodd" d="M12 3.2A8.8 8.8 0 1 0 12 20.8A8.8 8.8 0 1 0 12 3.2Z' +
      'M10.9 16.5L7.1 12.7L8.7 11.1L10.9 13.3L15.5 8.7L17.1 10.3Z"/>',
  },

  plus: { paths: '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>' },

  magnifyingglass: {
    paths: '<circle cx="10.4" cy="10.4" r="6.2"/><line x1="14.8" y1="14.8" x2="19.6" y2="19.6"/>',
  },

  location: { paths: '<path d="M3.4 11.2L21 3L12.8 20.8L11 12.9Z"/>' },

  'location.slash': {
    paths: '<path d="M3.4 11.2L21 3L12.8 20.8L11 12.9Z"/><line x1="4.2" y1="4.2" x2="19.8" y2="19.8"/>',
  },

  map: {
    paths:
      '<path d="M3.6 6.4L9.2 4.2L14.8 6.4L20.4 4.2V17.6L14.8 19.8L9.2 17.6L3.6 19.8Z"/>' +
      '<line x1="9.2" y1="4.2" x2="9.2" y2="17.6"/>' +
      '<line x1="14.8" y1="6.4" x2="14.8" y2="19.8"/>',
  },

  // ---------------------------------------------------------------- alerting

  bell: {
    paths:
      '<path d="M6.4 17.4C7.4 16.4 7.6 15 7.6 13.4V11.2A4.4 4.4 0 0 1 16.4 11.2V13.4C16.4 15 16.6 16.4 17.6 17.4Z"/>' +
      '<path d="M10.2 20.2A2.1 2.1 0 0 0 13.8 20.2"/>',
  },

  'bell.fill': {
    filled: true,
    paths:
      '<path d="M6.4 17.4C7.4 16.4 7.6 15 7.6 13.4V11.2A4.4 4.4 0 0 1 16.4 11.2V13.4C16.4 15 16.6 16.4 17.6 17.4Z"/>' +
      '<path d="M9.8 19.4A2.4 2.4 0 0 0 14.2 19.4Z"/>',
  },

  'bell.slash': {
    paths:
      '<path d="M6.4 17.4C7.4 16.4 7.6 15 7.6 13.4V11.2A4.4 4.4 0 0 1 16.4 11.2V13.4C16.4 15 16.6 16.4 17.6 17.4Z"/>' +
      '<path d="M10.2 20.2A2.1 2.1 0 0 0 13.8 20.2"/>' +
      '<line x1="4.4" y1="4.4" x2="19.6" y2="19.6"/>',
  },

  'exclamationmark.triangle': {
    paths:
      '<path d="M12 4.2L21.4 19.4H2.6Z"/>' +
      '<line x1="12" y1="10" x2="12" y2="14.4"/>' +
      dot(12, 17, 0.9),
  },

  'exclamationmark.triangle.fill': {
    filled: true,
    paths:
      '<path fill-rule="evenodd" d="M12 3.6L22 19.8H2Z' +
      'M11.1 9.4H12.9V14.8H11.1Z' +
      'M12 15.9A1.2 1.2 0 1 0 12 18.3A1.2 1.2 0 1 0 12 15.9Z"/>',
  },

  'info.circle': {
    paths: '<circle cx="12" cy="12" r="8.6"/><line x1="12" y1="11" x2="12" y2="16.4"/>' + dot(12, 8, 0.9),
  },

  'questionmark.circle': {
    paths:
      '<circle cx="12" cy="12" r="8.6"/>' +
      '<path d="M9.5 9.6A2.6 2.6 0 1 1 12 13.2V14.8"/>' +
      dot(12, 17.2, 0.9),
  },

  clock: {
    paths: '<circle cx="12" cy="12" r="8.6"/><polyline points="12 7.2 12 12 15.6 14.2"/>',
  },

  // ------------------------------------------------------------ lists, files

  'list.bullet': {
    paths:
      dot(5.4, 7.2, 1.1) + dot(5.4, 12, 1.1) + dot(5.4, 16.8, 1.1) +
      '<line x1="9.6" y1="7.2" x2="19.4" y2="7.2"/>' +
      '<line x1="9.6" y1="12" x2="19.4" y2="12"/>' +
      '<line x1="9.6" y1="16.8" x2="19.4" y2="16.8"/>',
  },

  'line.3.horizontal': {
    paths:
      '<line x1="4" y1="7.2" x2="20" y2="7.2"/>' +
      '<line x1="4" y1="12" x2="20" y2="12"/>' +
      '<line x1="4" y1="16.8" x2="20" y2="16.8"/>',
  },

  ellipsis: { paths: dot(5.6, 12, 1.5) + dot(12, 12, 1.5) + dot(18.4, 12, 1.5) },

  'doc.text': {
    paths:
      '<path d="M6.4 3.6H13.6L18.8 8.8V19.2A1.2 1.2 0 0 1 17.6 20.4H6.4A1.2 1.2 0 0 1 5.2 19.2V4.8A1.2 1.2 0 0 1 6.4 3.6Z"/>' +
      '<polyline points="13.6 3.6 13.6 8.8 18.8 8.8"/>' +
      '<line x1="8.2" y1="12.4" x2="15.8" y2="12.4"/>' +
      '<line x1="8.2" y1="15.2" x2="15.8" y2="15.2"/>' +
      '<line x1="8.2" y1="18" x2="13.4" y2="18"/>',
  },

  calendar: {
    paths:
      '<path d="M6.6 6.8H17.4A2.2 2.2 0 0 1 19.6 9V17.8A2.2 2.2 0 0 1 17.4 20H6.6A2.2 2.2 0 0 1 4.4 17.8V9A2.2 2.2 0 0 1 6.6 6.8Z"/>' +
      '<line x1="4.4" y1="11.4" x2="19.6" y2="11.4"/>' +
      '<line x1="8.6" y1="3.6" x2="8.6" y2="8"/>' +
      '<line x1="15.4" y1="3.6" x2="15.4" y2="8"/>',
  },

  bookmark: {
    paths: '<path d="M7 3.8H17A1.4 1.4 0 0 1 18.4 5.2V20.6L12 16.2L5.6 20.6V5.2A1.4 1.4 0 0 1 7 3.8Z"/>',
  },

  trash: {
    paths:
      '<line x1="4.6" y1="7.2" x2="19.4" y2="7.2"/>' +
      '<path d="M9.4 7.2V5.4A1.6 1.6 0 0 1 11 3.8H13A1.6 1.6 0 0 1 14.6 5.4V7.2"/>' +
      '<path d="M6.6 7.2L7.6 19.4A1.8 1.8 0 0 0 9.4 21.2H14.6A1.8 1.8 0 0 0 16.4 19.4L17.4 7.2"/>' +
      '<line x1="10.4" y1="10.6" x2="10.4" y2="17.6"/>' +
      '<line x1="13.6" y1="10.6" x2="13.6" y2="17.6"/>',
  },

  tray: {
    paths:
      '<path d="M3.8 14.8H8.4L10 17.2H14L15.6 14.8H20.2V18.6A1.8 1.8 0 0 1 18.4 20.4H5.6A1.8 1.8 0 0 1 3.8 18.6Z"/>' +
      '<path d="M3.8 14.8L6.6 6.2A1.8 1.8 0 0 1 8.3 5H15.7A1.8 1.8 0 0 1 17.4 6.2L20.2 14.8"/>',
  },

  'tray.full': {
    paths:
      '<path d="M3.8 14.8H8.4L10 17.2H14L15.6 14.8H20.2V18.6A1.8 1.8 0 0 1 18.4 20.4H5.6A1.8 1.8 0 0 1 3.8 18.6Z"/>' +
      '<path d="M3.8 14.8L6.6 6.2A1.8 1.8 0 0 1 8.3 5H15.7A1.8 1.8 0 0 1 17.4 6.2L20.2 14.8"/>' +
      '<line x1="8.4" y1="8.6" x2="15.6" y2="8.6"/>' +
      '<line x1="7.6" y1="11.6" x2="16.4" y2="11.6"/>',
  },

  paperplane: {
    paths: '<path d="M21.2 3.2L2.6 10.4L10.4 13.6L13.6 21.4Z"/><line x1="10.4" y1="13.6" x2="21.2" y2="3.2"/>',
  },

  paintpalette: {
    paths:
      '<path d="M12 3.4A8.6 8.6 0 1 0 12 20.6C13.2 20.6 13.8 19.8 13.8 19C13.8 18 13 17.4 13 16.4C13 15.2 14 14.4 15.2 14.4H17.2A3.4 3.4 0 0 0 20.6 11C20.6 6.8 16.8 3.4 12 3.4Z"/>' +
      dot(8, 8.6, 1.05) + dot(12.6, 7, 1.05) + dot(16.4, 9.4, 1.05) + dot(6.8, 13.2, 1.05),
  },

  'arrow.clockwise': {
    paths: '<path d="M17.66 6.34A8 8 0 1 1 12 4"/><polyline points="9.2 2.6 12 4 9.2 5.4"/>',
  },

  'square.and.arrow.down': {
    paths:
      '<path d="M6.6 13.6V18.4A2 2 0 0 0 8.6 20.4H15.4A2 2 0 0 0 17.4 18.4V13.6"/>' +
      '<line x1="12" y1="3.6" x2="12" y2="15.4"/>' +
      '<polyline points="7.8 11.2 12 15.4 16.2 11.2"/>',
  },

  // ------------------------------------------------------------------- radio

  'antenna.radiowaves.left.and.right': {
    paths:
      '<circle cx="12" cy="9.6" r="1.9"/>' +
      '<polyline points="10.6 20.4 12 11.5 13.4 20.4"/>' +
      '<path d="M8.2 13.4A5.4 5.4 0 0 1 8.2 5.8"/>' +
      '<path d="M15.8 5.8A5.4 5.4 0 0 1 15.8 13.4"/>' +
      '<path d="M5.2 16.4A9.4 9.4 0 0 1 5.2 2.8"/>' +
      '<path d="M18.8 2.8A9.4 9.4 0 0 1 18.8 16.4"/>',
  },

  'dot.radiowaves.left.and.right': {
    paths:
      '<circle cx="12" cy="12" r="2"/>' +
      '<path d="M8.4 15.6A5.4 5.4 0 0 1 8.4 8.4"/>' +
      '<path d="M15.6 8.4A5.4 5.4 0 0 1 15.6 15.6"/>' +
      '<path d="M5 18.8A9.4 9.4 0 0 1 5 5.2"/>' +
      '<path d="M19 5.2A9.4 9.4 0 0 1 19 18.8"/>',
  },

  // Bluetooth stand-in: three rightward waves. The Bluetooth rune is a
  // trademark, so the set never draws it.
  'wave.3.right': {
    paths:
      '<path d="M7.4 8.2A5.4 5.4 0 0 1 7.4 15.8"/>' +
      '<path d="M11.4 6.2A8 8 0 0 1 11.4 17.8"/>' +
      '<path d="M15.4 4.2A10.6 10.6 0 0 1 15.4 19.8"/>',
  },

  'cable.connector': {
    paths:
      '<path d="M9 4.4H15A2 2 0 0 1 17 6.4V13.6A2 2 0 0 1 15 15.6H9A2 2 0 0 1 7 13.6V6.4A2 2 0 0 1 9 4.4Z"/>' +
      '<line x1="9.8" y1="4.4" x2="9.8" y2="2.4"/>' +
      '<line x1="14.2" y1="4.4" x2="14.2" y2="2.4"/>' +
      '<line x1="9.6" y1="11.6" x2="14.4" y2="11.6"/>' +
      '<line x1="12" y1="15.6" x2="12" y2="21.4"/>',
  },

  'wifi.slash': {
    paths:
      '<path d="M4 10.4A11.2 11.2 0 0 1 20 10.4"/>' +
      '<path d="M7.2 13.6A7 7 0 0 1 16.8 13.6"/>' +
      '<path d="M9.8 16.6A3.2 3.2 0 0 1 14.2 16.6"/>' +
      dot(12, 19.8, 1) +
      '<line x1="4.2" y1="4.2" x2="19.8" y2="19.8"/>',
  },

  globe: {
    paths:
      '<circle cx="12" cy="12" r="8.6"/>' +
      '<path d="M12 3.4A4.4 8.6 0 0 0 12 20.6A4.4 8.6 0 0 0 12 3.4Z"/>' +
      '<line x1="3.9" y1="9.4" x2="20.1" y2="9.4"/>' +
      '<line x1="3.9" y1="14.6" x2="20.1" y2="14.6"/>',
  },

  satellite: {
    paths:
      '<path d="M10.2 9.6H13.8V14.4H10.2Z"/>' +
      '<path d="M3.2 10.2H9.4V13.8H3.2Z"/>' +
      '<path d="M14.6 10.2H20.8V13.8H14.6Z"/>' +
      '<line x1="6.3" y1="10.2" x2="6.3" y2="13.8"/>' +
      '<line x1="17.7" y1="10.2" x2="17.7" y2="13.8"/>' +
      '<line x1="12" y1="9.6" x2="12" y2="6.6"/>' +
      '<circle cx="12" cy="4.9" r="1.7"/>' +
      '<line x1="12" y1="14.4" x2="12" y2="17.4"/>',
  },
};

/**
 * Day/night and weight variants that are the same picture. `of` is the drawing
 * to reuse; `filled: true` renders it with fills instead of strokes.
 *
 * Only closed silhouettes are aliased as fills — a glyph whose meaning lives in
 * its interior strokes (`bell.fill`, `checkmark.circle.fill`,
 * `exclamationmark.triangle.fill`) gets its own even-odd drawing above.
 */
export const ALIASES = {
  'cloud.fill': { of: 'cloud', filled: true },
  'cloud.sun.fill': { of: 'cloud.sun', filled: true },
  'cloud.moon.fill': { of: 'cloud.moon', filled: true },
  'sun.max.fill': { of: 'sun.max', filled: true },
  'moon.stars.fill': { of: 'moon.stars', filled: true },
  'location.fill': { of: 'location', filled: true },
  'drop.fill': { of: 'drop', filled: true },
  'flame.fill': { of: 'flame', filled: true },
  'bookmark.fill': { of: 'bookmark', filled: true },
  'paperplane.fill': { of: 'paperplane', filled: true },
  // Sky 12 (mist) renders as fog; the wire distinguishes them, the picture does not.
  'cloud.mist': { of: 'cloud.fog' },
  'sun.min': { of: 'sun.max' },
  'thermometer.low': { of: 'thermometer.medium' },
  'thermometer.high': { of: 'thermometer.medium' },
  'arrow.triangle.2.circlepath': { of: 'arrow.clockwise' },
  'checkmark.circle.filled': { of: 'checkmark.circle.fill' },
};

const PLACEHOLDER = '<circle cx="12" cy="12" r="4.4"/>';

/** Resolve a name through the alias table. Returns null for an unknown name. */
function resolve(name) {
  const direct = ICONS[name];
  if (direct) return { paths: direct.paths, filled: Boolean(direct.filled) };
  const alias = ALIASES[name];
  if (!alias) return null;
  const target = ICONS[alias.of] || (ALIASES[alias.of] ? resolve(alias.of) : null);
  if (!target) return null;
  return {
    paths: target.paths,
    filled: alias.filled !== undefined ? Boolean(alias.filled) : Boolean(target.filled),
  };
}

/** True when `name` resolves to a drawing, directly or through an alias. */
export function hasIcon(name) {
  return resolve(name) !== null;
}

const warned = new Set();

function warnOnce(name) {
  if (warned.has(name)) return;
  warned.add(name);
  if (typeof console !== 'undefined' && console.warn) {
    console.warn(`[icons] no drawing for "${name}" — showing the placeholder dot`);
  }
}

const SVG_NS = 'http://www.w3.org/2000/svg';

/**
 * Build an `<svg>` element for `name`.
 *
 * An unknown name never throws: it returns a small circle so the gap is
 * obvious while developing, and warns once per name so a loop does not flood
 * the console.
 */
export function icon(name, { size = 20, label = null, className = '' } = {}) {
  if (typeof document === 'undefined') {
    throw new Error('icon() needs a DOM; import ICONS/hasIcon directly under Node');
  }
  const found = resolve(name);
  if (!found) warnOnce(name);
  const drawing = found || { paths: PLACEHOLDER, filled: false };

  const el = document.createElementNS(SVG_NS, 'svg');
  el.setAttribute('viewBox', '0 0 24 24');
  el.setAttribute('width', String(size));
  el.setAttribute('height', String(size));
  el.setAttribute('class', className ? `icon ${className}` : 'icon');
  if (drawing.filled) {
    el.setAttribute('fill', 'currentColor');
    el.setAttribute('stroke', 'none');
  } else {
    el.setAttribute('fill', 'none');
    el.setAttribute('stroke', 'currentColor');
    el.setAttribute('stroke-width', '1.75');
    el.setAttribute('stroke-linecap', 'round');
    el.setAttribute('stroke-linejoin', 'round');
  }
  if (label) {
    el.setAttribute('role', 'img');
    el.setAttribute('aria-label', label);
  } else {
    el.setAttribute('aria-hidden', 'true');
  }
  el.innerHTML = drawing.paths;
  return el;
}

/** Every name this module answers to, drawings first then aliases. */
export function iconNames() {
  return [...Object.keys(ICONS), ...Object.keys(ALIASES)].sort();
}

export default { ICONS, ALIASES, icon, hasIcon, iconNames };
