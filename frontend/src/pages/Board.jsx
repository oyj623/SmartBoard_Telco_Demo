/**
 * The AI Board.
 *
 * Everything on this page that is Nusatel-specific is data: which panels the
 * board opens with, which sections they sit in, and how the map is configured.
 * The board itself — the grid, the chat column, the panel frames, the demo rail
 * — is `BoardShell` from the vendored SmartBoard runtime, and it does not know
 * what a cell site is.
 *
 * The starting panels go through POST /api/board/query, which is the same
 * validate → guard → compile → scope → execute path a model-issued query takes,
 * with no model involved. That is why the assistant can restyle a panel it did
 * not draw, and why Undo steps back through both kinds of change.
 */

import { useCallback, useMemo, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import 'leaflet/dist/leaflet.css'

import BoardShell from '../smartboard/components/BoardShell.jsx'
import { useBoard } from '../smartboard/components/useBoard.js'
import { VizRegistry } from '../smartboard/client.js'
import { registerAll } from '../smartboard/adapters/echarts.js'
import { registerMaps } from '../smartboard/adapters/leaflet.js'
import { registerControls } from '../smartboard/adapters/controls.js'
import useAuthStore from '../store/authStore'
import useTheme from '../hooks/useTheme'
import demoScript from '../demoScript.js'

const API_ROOT = '/api/board'

/** Malaysia, framed to hold the peninsula and Borneo at once. */
const MAP_VIEW = { center: [4.0, 109.0], zoom: 5 }

export default function Board() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const { toggle: toggleTheme, isDark } = useTheme()
  const { user, token, logout } = useAuthStore()

  // A function, not a snapshot, so a refreshed token is picked up mid-session
  // without rebuilding the client.
  const tokenRef = useRef(token)
  tokenRef.current = token
  const headers = useCallback(
    () => (tokenRef.current ? { Authorization: `Bearer ${tokenRef.current}` } : {}),
    [],
  )

  const locale = i18n.language === 'bm' ? 'ms' : (i18n.language || 'en').slice(0, 2)

  // Charts and both map kinds. Every kind the assistant may name is registered
  // here; anything it names that is not registered simply does not render.
  const registry = useMemo(
    () =>
      registerControls(
        registerMaps(registerAll(new VizRegistry()), {
          headers,
          ...MAP_VIEW,
          regions: { url: '/api/geo/states', featureKey: 'state_code' },
        }),
      ),
    [headers],
  )

  const sections = useMemo(
    () => [
      {
        id: 'sec_headline',
        title: { en: t('sections.headline') },
        subtitle: { en: t('sections.headlineSub') },
      },
      { id: 'sec_controls', title: { en: t('sections.controls') } },
      { id: 'sec_network', title: { en: t('sections.network') } },
      { id: 'sec_commercial', title: { en: t('sections.commercial') } },
    ],
    [t],
  )

  /**
   * The board a person lands on.
   *
   * Every `style` here is something the assistant can also set, and every
   * `layout` is something it can rearrange. Nothing on the starting board uses
   * a capability the conversation cannot reach.
   */
  const panels = useMemo(
    () => [
      // -- headline ---------------------------------------------------------
      {
        panel_id: 'p_stat_availability',
        ir: { metrics: ['availability_pct'], label: 'Availability' },
        viz: 'stat',
        encoding: { value: 'availability_pct' },
        title: { en: t('panels.availability') },
        style: { reference_line: 99.0, reference_label: 'SLA 99%' },
        layout: { col_span: 3, section: 'sec_headline' },
      },
      {
        panel_id: 'p_stat_download',
        ir: { metrics: ['avg_download_mbps'], label: 'Download speed' },
        viz: 'stat',
        encoding: { value: 'avg_download_mbps' },
        title: { en: t('panels.download') },
        style: { reference_line: 35, reference_label: '4G target 35' },
        layout: { col_span: 3, section: 'sec_headline' },
      },
      {
        panel_id: 'p_stat_churn',
        ir: { metrics: ['churn_rate_pct'], label: 'Churn rate' },
        viz: 'stat',
        encoding: { value: 'churn_rate_pct' },
        title: { en: t('panels.churn') },
        style: { reference_line: 1.6, reference_label: 'blended target' },
        layout: { col_span: 3, section: 'sec_headline' },
      },
      {
        panel_id: 'p_stat_alarms',
        ir: { metrics: ['open_alarm_count'], label: 'Open alarms' },
        viz: 'stat',
        encoding: { value: 'open_alarm_count' },
        title: { en: t('panels.openAlarms') },
        layout: { col_span: 3, section: 'sec_headline' },
      },

      // -- controls ---------------------------------------------------------
      // A filter with no query behind it. It reads the catalog rather than a
      // result, and clicking a chip emits the same `set_filter` the assistant
      // emits — so the board can be narrowed by hand or by asking, and neither
      // path is the special case. It is on the starting board because a control
      // nobody knows exists is a control nobody uses.
      {
        panel_id: 'p_filters',
        viz: 'filter_panel',
        encoding: { dims: ['state', 'technology', 'plan', 'month'] },
        title: { en: t('panels.filters') },
        note: { en: t('panels.filtersNote') },
        layout: { col_span: 12, section: 'sec_controls' },
      },

      // -- network ----------------------------------------------------------
      {
        panel_id: 'p_traffic_trend',
        ir: {
          metrics: ['traffic_tb'],
          dimensions: ['month'],
          time_range: { last_n: 12, grain: 'month' },
          label: 'Data traffic by month',
        },
        viz: 'line',
        encoding: { x: 'month', y: ['traffic_tb'] },
        title: { en: t('panels.trafficTrend') },
        note: { en: t('panels.trafficNote') },
        style: { smooth: true },
        layout: { col_span: 8, section: 'sec_network' },
      },
      {
        panel_id: 'p_state_speed',
        ir: {
          metrics: ['avg_download_mbps'],
          dimensions: ['state'],
          limit: 20,
          label: 'Download speed by state',
        },
        viz: 'bar',
        encoding: { x: 'state', y: ['avg_download_mbps'] },
        title: { en: t('panels.stateSpeed') },
        note: { en: t('panels.stateSpeedNote') },
        // Horizontal, because "Negeri Sembilan" is unreadable rotated 32°.
        style: { horizontal: true, sort: 'asc', grid: false, reference_line: 35 },
        layout: { col_span: 4, section: 'sec_network' },
      },
      {
        panel_id: 'p_coverage_map',
        ir: {
          metrics: ['share_5g_pct'],
          dimensions: ['state_code'],
          limit: 20,
          label: '5G share by state',
        },
        viz: 'map_regions',
        encoding: { geo: 'state_code', value: 'share_5g_pct' },
        title: { en: t('panels.coverageMap') },
        note: { en: t('panels.coverageMapNote') },
        layout: { col_span: 7, row_span: 2, section: 'sec_network' },
      },
      {
        panel_id: 'p_site_map',
        ir: {
          metrics: ['avg_download_mbps'],
          dimensions: ['site_location'],
          limit: 2600,
          label: 'Sites by download speed',
        },
        viz: 'map_points',
        encoding: { geo: 'site_location', value: 'avg_download_mbps', size: 'avg_download_mbps' },
        title: { en: t('panels.siteMap') },
        note: { en: t('panels.siteMapNote') },
        layout: { col_span: 5, row_span: 2, section: 'sec_network' },
      },

      // -- commercial -------------------------------------------------------
      {
        panel_id: 'p_ticket_mix',
        ir: {
          metrics: ['ticket_count'],
          dimensions: ['ticket_category', 'channel'],
          limit: 40,
          label: 'Tickets by category and channel',
        },
        viz: 'stacked_bar',
        encoding: { x: 'ticket_category', y: ['ticket_count'], series: 'channel' },
        title: { en: t('panels.ticketMix') },
        style: { legend: true, sort: 'desc' },
        layout: { col_span: 7, section: 'sec_commercial' },
      },
      {
        panel_id: 'p_revenue_mix',
        execOnly: true,
        ir: {
          metrics: ['revenue_myr'],
          dimensions: ['service_line'],
          limit: 10,
          label: 'Revenue by service line',
        },
        viz: 'donut',
        encoding: { x: 'service_line', value: 'revenue_myr' },
        title: { en: t('panels.revenueMix') },
        layout: { col_span: 5, section: 'sec_commercial' },
      },
    ],
    [t],
  )

  // A regional manager is not offered the revenue panel. The server would refuse
  // the query anyway — this just avoids drawing a panel that could only fail.
  const panelFilter = useCallback((panel, health) => !panel.execOnly || health?.role === 'exec', [])

  const board = useBoard({ baseUrl: API_ROOT, headers, registry, sections, panels, panelFilter })

  const labels = useMemo(
    () => ({
      chat: {
        title: t('board.assistant'),
        placeholder: t('board.placeholder'),
        placeholderSelected: t('board.placeholderSelected'),
        footer: t('board.footer'),
      },
      empty: {
        title: t('board.emptyTitle'),
        body: t('board.emptyBody'),
        bootingTitle: t('board.bootingTitle'),
        bootingBody: t('board.bootingBody'),
        errorTitle: t('board.errorTitle'),
      },
    }),
    [t],
  )

  const signOut = () => {
    logout()
    navigate('/login', { replace: true })
  }

  const toolbarExtra = (
    <>
      <select
        className="btn btn-ghost"
        style={{ fontSize: 10, padding: '3px 6px' }}
        value={i18n.language.slice(0, 2) === 'zh' ? 'zh' : i18n.language === 'bm' ? 'bm' : 'en'}
        onChange={(e) => i18n.changeLanguage(e.target.value)}
        title="Language"
      >
        <option value="en">EN</option>
        <option value="zh">中文</option>
        <option value="bm">BM</option>
      </select>
      <button
        className="btn btn-ghost"
        style={{ fontSize: 10 }}
        onClick={toggleTheme}
        title="Light or dark"
      >
        {isDark ? '☾' : '☀'}
      </button>
      <span className="chip" style={{ fontSize: 9 }} title={user?.name}>
        {user?.name || user?.username}
      </span>
      <button className="btn btn-ghost" style={{ fontSize: 10 }} onClick={signOut}>
        {t('board.logout')}
      </button>
    </>
  )

  return (
    <BoardShell
      board={board}
      locale={locale}
      headers={headers}
      labels={labels}
      demoScript={demoScript}
      toolbarExtra={toolbarExtra}
    />
  )
}
