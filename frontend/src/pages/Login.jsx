import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import useAuthStore from '../store/authStore'
import useTheme from '../hooks/useTheme'

const ACCOUNTS = [
  { username: 'hq', password: 'hq', roleKey: 'roleExec' },
  { username: 'north', password: 'north', roleKey: 'roleNorth' },
  { username: 'borneo', password: 'borneo', roleKey: 'roleBorneo' },
]

export default function Login() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  const login = useAuthStore((s) => s.login)
  const { toggle: toggleTheme, isDark } = useTheme()

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  async function submit(event, creds) {
    event?.preventDefault()
    const u = creds?.username ?? username
    const p = creds?.password ?? password
    if (!u || !p || busy) return

    setBusy(true)
    setError(null)
    try {
      await login(u, p)
      navigate('/board', { replace: true })
    } catch (err) {
      setError(err.response?.status === 401 ? t('login.failed') : err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={styles.page}>
      <div style={styles.topbar}>
        <select
          className="btn btn-ghost"
          style={{ fontSize: 11, padding: '4px 8px' }}
          value={i18n.language.slice(0, 2) === 'zh' ? 'zh' : i18n.language === 'bm' ? 'bm' : 'en'}
          onChange={(e) => i18n.changeLanguage(e.target.value)}
        >
          <option value="en">EN</option>
          <option value="zh">中文</option>
          <option value="bm">BM</option>
        </select>
        <button className="btn btn-ghost" style={{ fontSize: 11 }} onClick={toggleTheme}>
          {isDark ? '☾' : '☀'}
        </button>
      </div>

      <div style={styles.card}>
        <div style={styles.brandRow}>
          <span style={styles.mark} aria-hidden="true" />
          <div>
            <h1 style={styles.brand}>{t('brand')}</h1>
            <div style={styles.tagline}>{t('tagline')}</div>
          </div>
        </div>

        <p style={styles.subtitle}>{t('login.subtitle')}</p>

        <form onSubmit={submit} style={styles.form}>
          <label style={styles.label}>
            {t('login.username')}
            <input
              style={styles.input}
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoFocus
            />
          </label>
          <label style={styles.label}>
            {t('login.password')}
            <input
              style={styles.input}
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </label>

          {error && <div style={styles.error}>{error}</div>}

          <button className="btn btn-primary" style={styles.submit} disabled={busy}>
            {busy ? t('login.signingIn') : t('login.submit')}
          </button>
        </form>

        <div style={styles.divider}>
          <span>{t('login.demoAccounts')}</span>
        </div>

        <div style={styles.accounts}>
          {ACCOUNTS.map((acct) => (
            <button
              key={acct.username}
              style={styles.account}
              disabled={busy}
              onClick={(e) => submit(e, acct)}
            >
              <span style={styles.acctName}>
                {acct.username} / {acct.password}
              </span>
              <span style={styles.acctRole}>{t(`login.${acct.roleKey}`)}</span>
            </button>
          ))}
        </div>

        <p style={styles.note}>{t('login.note')}</p>
      </div>
    </div>
  )
}

const styles = {
  page: {
    minHeight: '100vh',
    display: 'grid',
    placeItems: 'center',
    background: 'var(--bg-0)',
    padding: 20,
    position: 'relative',
  },
  topbar: { position: 'absolute', top: 16, right: 20, display: 'flex', gap: 6 },
  card: {
    width: '100%',
    maxWidth: 420,
    background: 'var(--bg-1)',
    border: '1px solid var(--line)',
    borderRadius: 'var(--r-4)',
    padding: '30px 32px 26px',
  },
  brandRow: { display: 'flex', alignItems: 'center', gap: 12 },
  mark: {
    width: 30,
    height: 30,
    borderRadius: 8,
    flexShrink: 0,
    background: 'var(--accent)',
    boxShadow: '0 0 0 4px var(--accent-soft)',
  },
  brand: { margin: 0, fontSize: 21, fontWeight: 700, letterSpacing: '-0.02em', color: 'var(--fg)' },
  tagline: {
    fontFamily: 'var(--font-mono)',
    fontSize: 10,
    letterSpacing: '0.1em',
    textTransform: 'uppercase',
    color: 'var(--fg-3)',
    marginTop: 1,
  },
  subtitle: { fontSize: 13, color: 'var(--fg-2)', margin: '18px 0 0', lineHeight: 1.5 },
  form: { display: 'flex', flexDirection: 'column', gap: 12, marginTop: 22 },
  label: {
    display: 'flex',
    flexDirection: 'column',
    gap: 5,
    fontSize: 11,
    color: 'var(--fg-3)',
    fontWeight: 500,
  },
  input: {
    background: 'var(--bg-2)',
    border: '1px solid var(--line)',
    borderRadius: 'var(--r-2)',
    padding: '9px 11px',
    fontSize: 13,
    color: 'var(--fg)',
  },
  submit: { justifyContent: 'center', padding: '9px 14px', fontSize: 13, marginTop: 4 },
  error: {
    background: 'var(--crit-soft)',
    border: '1px solid var(--crit)',
    borderRadius: 'var(--r-2)',
    padding: '7px 10px',
    fontSize: 12,
    color: 'var(--fg)',
  },
  divider: {
    display: 'flex',
    alignItems: 'center',
    gap: 10,
    margin: '24px 0 12px',
    fontSize: 10,
    fontFamily: 'var(--font-mono)',
    letterSpacing: '0.1em',
    textTransform: 'uppercase',
    color: 'var(--fg-4)',
  },
  accounts: { display: 'flex', flexDirection: 'column', gap: 6 },
  account: {
    display: 'flex',
    flexDirection: 'column',
    alignItems: 'flex-start',
    gap: 2,
    textAlign: 'left',
    background: 'var(--bg-2)',
    border: '1px solid var(--line)',
    borderRadius: 'var(--r-2)',
    padding: '8px 11px',
    cursor: 'pointer',
    fontFamily: 'inherit',
  },
  acctName: { fontFamily: 'var(--font-mono)', fontSize: 12, color: 'var(--accent)', fontWeight: 500 },
  acctRole: { fontSize: 10.5, color: 'var(--fg-3)' },
  note: { fontSize: 10.5, color: 'var(--fg-4)', lineHeight: 1.55, margin: '18px 0 0' },
}
