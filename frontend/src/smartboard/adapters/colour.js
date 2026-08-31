/**
 * Colour resolution, shared by every renderer.
 *
 * This module exists because of a real bug, not for tidiness.
 *
 * Design tokens are commonly authored in `oklch()`. CSS handles that
 * fine. ECharts does not — it parses colour strings itself, in zrender, whose
 * parser predates oklch. Leaflet's canvas renderer hands colours to the browser
 * and mostly copes, but it derives nothing from them, so it hid the problem
 * rather than avoiding it. An unparseable colour renders once by luck and then
 * resolves to transparent the moment a library has to compute a second colour
 * from it — which ECharts does on hover. The symptom was a bar or a donut slice
 * vanishing under the cursor.
 *
 * Painting one pixel and reading it back makes the browser do the conversion,
 * so anything CSS accepts — oklch, lab, color-mix, a named colour — reaches the
 * chart libraries as plain rgb.
 */

const _probe = (() => {
  if (typeof document === 'undefined') return null;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = 1;
  return canvas.getContext('2d', { willReadFrequently: true });
})();

const _cache = new Map();

/** Any CSS colour → `rgb()` / `rgba()`. Cached; falls back rather than throwing. */
export function resolveColor(value, fallback = '#888888') {
  if (!value) return fallback;
  const raw = String(value).trim();
  if (!raw) return fallback;

  // Already something the chart libraries parse natively — skip the round trip.
  if (/^(#|rgba?\(|hsla?\()/i.test(raw)) return raw;

  if (_cache.has(raw)) return _cache.get(raw);
  if (!_probe) return fallback;

  let out = fallback;
  try {
    _probe.clearRect(0, 0, 1, 1);
    _probe.fillStyle = '#000000';
    _probe.fillStyle = raw; // silently ignored by the browser if unparseable
    _probe.fillRect(0, 0, 1, 1);
    const [r, g, b, a] = _probe.getImageData(0, 0, 1, 1).data;
    out = a === 255 ? `rgb(${r}, ${g}, ${b})` : `rgba(${r}, ${g}, ${b}, ${(a / 255).toFixed(3)})`;
  } catch {
    out = fallback;
  }

  _cache.set(raw, out);
  return out;
}

/**
 * A design token's current value, resolved to rgb.
 *
 * Tokens are read from `body`, not `documentElement` — the light theme is a
 * `body.theme-light` class, so the overrides only exist from body down. The
 * cache is dropped whenever that class changes.
 */
let _themeKey = '';
export function css(name, fallback) {
  const key = typeof document === 'undefined' ? '' : document.body.className;
  if (key !== _themeKey) {
    _themeKey = key;
    _cache.clear();
  }
  const raw = typeof document === 'undefined' ? '' : getComputedStyle(document.body).getPropertyValue(name).trim();
  return resolveColor(raw || fallback, fallback);
}

/** Fade a resolved colour. Used for de-emphasis, so it needs no second token. */
export function fade(rgb, alpha) {
  const m = String(rgb).match(/(\d+(\.\d+)?)/g);
  if (!m || m.length < 3) return rgb;
  return `rgba(${m[0]}, ${m[1]}, ${m[2]}, ${alpha})`;
}

export const CHART_TOKENS = ['--chart-1', '--chart-2', '--chart-3', '--chart-4', '--chart-5', '--chart-6'];
export const CHART_FALLBACKS = ['#4aa8d8', '#5fc9a0', '#e8b84a', '#e08a4a', '#9d8ae0', '#e05a7a'];

/**
 * Resolve a style's palette, or the theme default.
 *
 * `token:chart-2` resolves against the design tokens, which is why that form is
 * worth offering the assistant in the tool schema: a model-chosen colour can
 * still follow the light/dark switch.
 */
export function palette(style = {}) {
  const chosen = style.palette?.length ? style.palette : style.color ? [style.color] : null;
  if (chosen) {
    return chosen.map((c) =>
      String(c).startsWith('token:') ? css(`--${String(c).slice(6)}`, CHART_FALLBACKS[0]) : resolveColor(c, CHART_FALLBACKS[0]),
    );
  }
  return CHART_TOKENS.map((tok, i) => css(tok, CHART_FALLBACKS[i]));
}
