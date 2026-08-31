/**
 * Leaflet map adapters — the GIS half of the viz registry.
 *
 * Two entries on the menu the model picks from:
 *
 *   map_points    one circle per row, positioned from a geo dimension's
 *                 auto-expanded lat/lng columns, sized and coloured by a metric
 *   map_regions   a choropleth over GeoJSON polygons the app supplies, joined
 *                 to the result on a feature property
 *
 * The registry contract says a renderer receives rows, columns and an encoding
 * and returns a cleanup function. What it draws with them — including fetching
 * geometry the result set does not carry — is entirely the app's business. The
 * model has no say in any of it: it names `map_regions` and a metric; every
 * decision below is made here, in code you own and review.
 *
 * The registry is also the whole view-side security story. The model can emit
 * `viz: "iframe"` all day; nothing is registered under that name, so nothing
 * renders and nothing executes.
 *
 * Configuration arrives once, through `registerMaps(registry, config)`:
 *
 *   {
 *     headers: () => ({ Authorization: `Bearer ${token}` }),   // for geometry fetches
 *     center: [4.2, 108.6], zoom: 5,                           // initial camera
 *     tileUrl, attribution,                                    // basemap override
 *     regions: { url: '/api/geo/states', featureKey: 'state_code' },
 *   }
 */

import L from 'leaflet';
import { formatValue, t } from '../client.js';
import { css, fade, palette, CHART_FALLBACKS } from './colour.js';

const DEFAULT_TILES = {
  tileUrl: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
  attribution: 'Tiles &copy; Esri',
};

/**
 * Ramp from the cool end of the palette to the hot end.
 *
 * `style.palette` from the assistant replaces the stops, so "colour the map
 * green to red" is a restyle rather than a code change.
 */
function ramp(style = {}) {
  const custom = style.palette?.length >= 2 ? palette(style) : null;
  const stops = custom || [
    css('--chart-1', CHART_FALLBACKS[0]),
    css('--chart-2', CHART_FALLBACKS[1]),
    css('--chart-3', CHART_FALLBACKS[2]),
    css('--chart-4', CHART_FALLBACKS[3]),
    css('--chart-6', CHART_FALLBACKS[5]),
  ];
  const nodata = css('--fg-4', '#6b7280');
  return (v) => {
    if (!Number.isFinite(v)) return nodata;
    const i = Math.min(stops.length - 1, Math.max(0, Math.round(v * (stops.length - 1))));
    return stops[i];
  };
}

/**
 * Where a value sits between the low and high ends of the scale, 0..1.
 *
 * `style.y_min` / `style.y_max` pin the scale, which is what makes two maps
 * comparable. Without them the ramp is relative to whatever happens to be in
 * this result.
 */
function normaliser(rows, metric, style = {}) {
  const values = rows.map((r) => r[metric]).filter((v) => Number.isFinite(v));
  const lo = style.y_min ?? Math.min(...values);
  const hi = style.y_max ?? Math.max(...values);
  const span = hi - lo || 1;
  return (v) => (Number.isFinite(v) ? Math.min(1, Math.max(0, (v - lo) / span)) : NaN);
}

/**
 * The keys this panel should emphasise, or null for "everything is normal".
 *
 * The user's selection is sticky and wins; the assistant's highlight expires.
 * Identical rule to the ECharts adapter, deliberately — a region dimmed on the
 * map and a bar dimmed on a chart should mean the same thing.
 */
function emphasisKeys(store, panel) {
  if (!store || !panel) return null;

  const selected = store.getState().selection.filter((e) => e.panelId === panel.panelId && e.key != null);
  if (selected.length) return new Set(selected.map((e) => String(e.key)));

  const hl = store.getState().highlight;
  if (hl?.keys?.length && (!hl.panelId || hl.panelId === panel.panelId) && Date.now() <= hl.expiresAt) {
    return new Set(hl.keys.map(String));
  }
  return null;
}

/** Shared setup: a map, a basemap, a resize observer and a teardown. */
function mountMap(el, config = {}) {
  const { center = [4.2, 108.6], zoom = 5, tileUrl, attribution } = config;
  el.innerHTML = '';
  const host = document.createElement('div');
  host.style.cssText = 'position:absolute;inset:0;';
  el.style.position = 'relative';
  el.appendChild(host);

  const map = L.map(host, {
    center,
    zoom,
    zoomControl: true,
    attributionControl: false,
    preferCanvas: true,
    // The map lives inside a scrolling board. Wheel-zoom would eat the page
    // scroll every time the cursor crossed a map panel. Click the map first
    // and the wheel zooms; move away and it stops.
    scrollWheelZoom: false,
  });
  map.on('click focus', () => map.scrollWheelZoom.enable());
  map.on('mouseout blur', () => map.scrollWheelZoom.disable());
  L.tileLayer(tileUrl || DEFAULT_TILES.tileUrl, {
    attribution: attribution || DEFAULT_TILES.attribution,
    maxZoom: 18,
  }).addTo(map);

  const ro = new ResizeObserver(() => map.invalidateSize());
  ro.observe(el);
  setTimeout(() => map.invalidateSize(), 60);

  return {
    map,
    destroy() {
      ro.disconnect();
      map.remove();
    },
  };
}

/** A small legend so a colour ramp means something. */
function legend(map, { lo, hi, column, manifest, colourAt }) {
  const ctl = L.control({ position: 'bottomright' });
  ctl.onAdd = () => {
    const div = L.DomUtil.create('div', 'map-legend');
    const swatches = [0, 0.25, 0.5, 0.75, 1]
      .map((stop) => `<i style="background:${colourAt(stop)}"></i>`)
      .join('');
    div.innerHTML = `
      <div class="map-legend-bar">${swatches}</div>
      <div class="map-legend-ends">
        <span>${formatValue(lo, column, manifest)}</span>
        <span>${formatValue(hi, column, manifest)}</span>
      </div>`;
    return div;
  };
  ctl.addTo(map);
  return ctl;
}

// ---------------------------------------------------------------------------
// map_points
// ---------------------------------------------------------------------------

/**
 * One circle per row, positioned from the geo dimension's auto-expanded
 * lat/lng columns.
 *
 * The model asked for one geo identifier. The compiler turned that into three
 * columns (`<dim>`, `<dim>__lat`, `<dim>__lng`) because the manifest declared
 * where latitude and longitude live. That is why this renderer never has to
 * ask the model for coordinates, and why the model cannot get them wrong.
 */
export function mapPoints(el, ctx, config = {}) {
  const { rows, columns, encoding, manifest, store, panel, locale } = ctx;
  const style = panel?.style || {};

  const geoCol = columns.find((c) => c.lat_field) || columns.find((c) => c.role === 'dimension');
  if (!geoCol?.lat_field) {
    el.innerHTML =
      '<div class="panel-empty">This result has no geographic dimension. Query the geo dimension declared in the manifest.</div>';
    return () => {};
  }

  const metric =
    encoding.value || encoding.size || (encoding.y || [])[0] ||
    columns.find((c) => c.role === 'metric')?.id;
  const metricCol = columns.find((c) => c.id === metric) || {};
  const norm = normaliser(rows, metric, style);
  const colourAt = ramp(style);
  const values = rows.map((r) => r[metric]).filter(Number.isFinite);

  const points = rows
    .map((r) => ({ row: r, lat: r[geoCol.lat_field], lng: r[geoCol.lng_field] }))
    .filter((p) => Number.isFinite(p.lat) && Number.isFinite(p.lng));

  // Marker size has to fall as the point count rises. A radius that reads well
  // for two hundred features draws two thousand as one continuous smear, and a
  // map you cannot see through is worse than no map. Scale the radius, the
  // stroke and the fill together so dense maps stay readable and sparse ones
  // are unchanged.
  const density =
    points.length > 1500 ? 0.36 : points.length > 600 ? 0.55 : points.length > 250 ? 0.75 : 1;
  const radiusFor = (at) => (2.5 + 9 * (Number.isFinite(at) ? at : 0.3)) * density + 1.2;
  const strokeWidth = Math.max(0.4, 1.5 * density);
  const baseOpacity = style.opacity ?? (density < 0.6 ? 0.55 : 0.7);

  const { map, destroy } = mountMap(el, config);
  const markers = new Map();

  for (const { row, lat, lng } of points) {
    const key = String(row[geoCol.id]);
    const at = norm(row[metric]);
    const marker = L.circleMarker([lat, lng], {
      radius: radiusFor(at),
      color: colourAt(at),
      fillColor: colourAt(at),
      fillOpacity: baseOpacity,
      weight: strokeWidth,
    }).addTo(map);

    marker.bindTooltip(
      `<b>${key}</b><br/>${metricCol.label || metric}: ${formatValue(row[metric], metricCol, manifest)}`,
      { direction: 'top', className: 'map-tip' },
    );

    // Clicking a point SELECTS it. Same gesture, same store, same meaning as
    // clicking a bar — it becomes context for the next question rather than an
    // instruction about how the board should look.
    marker.on('click', (event) => {
      const native = event.originalEvent;
      L.DomEvent.stopPropagation(event);
      store?.toggleSelection(
        { panelId: panel?.panelId, key, label: key },
        !!(native?.ctrlKey || native?.metaKey),
      );
    });

    markers.set(key, { marker, at });
  }

  if (points.length) {
    map.fitBounds(L.latLngBounds(points.map((p) => [p.lat, p.lng])).pad(0.12), { maxZoom: 14 });
  }
  if (values.length) {
    legend(map, {
      lo: style.y_min ?? Math.min(...values),
      hi: style.y_max ?? Math.max(...values),
      column: metricCol,
      manifest,
      colourAt,
    });
  }

  // Clicking the basemap, not a point, selects the whole panel.
  map.on('click', (event) =>
    store?.toggleSelection(
      { panelId: panel?.panelId, key: null, label: t(panel?.title, locale) || panel?.panelId },
      !!(event.originalEvent?.ctrlKey || event.originalEvent?.metaKey),
    ),
  );

  /**
   * Repaint on highlight and map-focus rather than re-rendering.
   *
   * A highlight is not a re-query — it reaches into what is already drawn and
   * dims everything that is not the answer. Tearing the map down and rebuilding
   * it would lose the camera and flash the tiles.
   */
  const repaint = () => {
    const emph = emphasisKeys(store, panel);

    for (const [key, { marker, at }] of markers) {
      const on = !emph || emph.has(key);
      const base = colourAt(at);
      marker.setStyle({
        color: on ? (emph ? css('--high', '#e08a4a') : base) : fade(base, 0.35),
        fillColor: on ? base : fade(base, 0.2),
        fillOpacity: on ? baseOpacity : 0.08,
        // An emphasised mark keeps a legible stroke however dense the map is —
        // that is the whole point of emphasising it.
        weight: on && emph ? Math.max(2, strokeWidth * 2) : strokeWidth,
      });
      if (on && emph) marker.bringToFront();
    }

    const focus = store?.getState().mapFocus;
    if (focus && (!focus.panelId || focus.panelId === panel?.panelId) && focus.featureIds?.length) {
      const targets = focus.featureIds
        .map((id) => markers.get(String(id))?.marker.getLatLng())
        .filter(Boolean);
      if (targets.length) map.flyToBounds(L.latLngBounds(targets).pad(0.35), { duration: 0.8 });
    }
  };

  repaint();
  const unsubscribe = store?.subscribe(repaint) || (() => {});

  return () => {
    unsubscribe();
    destroy();
  };
}

// ---------------------------------------------------------------------------
// map_regions
// ---------------------------------------------------------------------------

// Cache the PROMISE per URL rather than the result: several map panels can
// mount in the same tick, and caching the value would still let them all miss.
const geometryPromises = new Map();

/**
 * Choropleth over app-supplied GeoJSON polygons.
 *
 * Geometry is fetched once from `config.regions.url` and joined to the result
 * on `feature.properties[config.regions.featureKey]` against the result's
 * first dimension column. The model supplied a metric and a dimension; it
 * never touches the geometry.
 */
export function mapRegions(config = {}) {
  const { url, featureKey = 'code' } = config.regions || {};
  const headersFn = config.headers;

  return function render(el, ctx) {
    const { rows, columns, encoding, manifest, store, panel, locale } = ctx;
    const style = panel?.style || {};

    if (!url) {
      el.innerHTML = '<div class="panel-empty">map_regions is not configured: no regions GeoJSON URL.</div>';
      return () => {};
    }

    const keyCol = columns.find((c) => c.role === 'dimension')?.id;
    const metric =
      encoding.value || (encoding.y || [])[0] || columns.find((c) => c.role === 'metric')?.id;
    const metricCol = columns.find((c) => c.id === metric) || {};
    const norm = normaliser(rows, metric, style);
    const colourAt = ramp(style);
    const byKey = new Map(rows.map((r) => [String(r[keyCol]), r]));

    const { map, destroy } = mountMap(el, config);
    let layer = null;
    let unsubscribe = () => {};
    let cancelled = false;

    (async () => {
      if (!geometryPromises.has(url)) {
        geometryPromises.set(
          url,
          fetch(url, { headers: typeof headersFn === 'function' ? headersFn() : {} }).then((res) => res.json()),
        );
      }
      const geometry = await geometryPromises.get(url);
      if (cancelled) return;

      const shapeStyle = (feature) => {
        const key = String(feature.properties[featureKey]);
        const row = byKey.get(key);
        const at = row ? norm(row[metric]) : NaN;
        const emph = emphasisKeys(store, panel);
        const on = !emph || emph.has(key);

        return {
          color: on && emph ? css('--high', '#e08a4a') : css('--line-strong', '#555'),
          weight: on && emph ? 2 : 0.8,
          fillColor: row ? colourAt(at) : css('--bg-3', '#333'),
          fillOpacity: row ? (on ? style.opacity ?? 0.72 : 0.1) : 0.06,
        };
      };

      layer = L.geoJSON(geometry, {
        style: shapeStyle,
        onEachFeature: (feature, lyr) => {
          const key = String(feature.properties[featureKey]);
          const name = feature.properties.name || key;
          const row = byKey.get(key);
          lyr.bindTooltip(
            `<b>${name}</b><br/>${
              row
                ? `${metricCol.label || metric}: ${formatValue(row[metric], metricCol, manifest)}`
                : 'no data in this result'
            }`,
            { direction: 'top', className: 'map-tip', sticky: true },
          );
          lyr.on('click', (event) => {
            const native = event.originalEvent;
            L.DomEvent.stopPropagation(event);
            store?.toggleSelection(
              { panelId: panel?.panelId, key, label: name },
              !!(native?.ctrlKey || native?.metaKey),
            );
          });
        },
      }).addTo(map);

      map.on('click', (event) =>
        store?.toggleSelection(
          { panelId: panel?.panelId, key: null, label: t(panel?.title, locale) || panel?.panelId },
          !!(event.originalEvent?.ctrlKey || event.originalEvent?.metaKey),
        ),
      );

      map.fitBounds(layer.getBounds().pad(0.05));

      const values = rows.map((r) => r[metric]).filter(Number.isFinite);
      if (values.length) {
        legend(map, {
          lo: style.y_min ?? Math.min(...values),
          hi: style.y_max ?? Math.max(...values),
          column: metricCol,
          manifest,
          colourAt,
        });
      }

      const repaint = () => {
        layer?.setStyle(shapeStyle);
        const focus = store?.getState().mapFocus;
        if (focus && (!focus.panelId || focus.panelId === panel?.panelId) && focus.featureIds?.length) {
          const wanted = new Set(focus.featureIds.map(String));
          const targets = [];
          layer.eachLayer((lyr) => {
            if (wanted.has(String(lyr.feature.properties[featureKey]))) targets.push(lyr.getBounds());
          });
          if (targets.length) {
            map.flyToBounds(targets.reduce((acc, b) => acc.extend(b)).pad(0.4), { duration: 0.8 });
          }
        }
      };
      unsubscribe = store?.subscribe(repaint) || (() => {});
    })();

    return () => {
      cancelled = true;
      unsubscribe();
      destroy();
    };
  };
}

/**
 * Register the map kinds.
 *
 * `map_regions` is only registered when the config declares a regions source —
 * a deployment with no polygon layer simply does not have the kind, and the
 * manifest should not enable it either.
 */
export function registerMaps(registry, config = {}) {
  registry.set('map_points', (el, ctx) => mapPoints(el, ctx, config));
  if (config.regions?.url) registry.set('map_regions', mapRegions(config));
  return registry;
}
