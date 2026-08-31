import { useEffect, useState } from 'react'

export default function useTheme() {
  const [theme, setTheme] = useState(
    () => localStorage.getItem('nusatel-theme') || 'theme-dark',
  )

  useEffect(() => {
    document.body.classList.remove('theme-dark', 'theme-light')
    document.body.classList.add(theme)
    localStorage.setItem('nusatel-theme', theme)
  }, [theme])

  const toggle = () => setTheme((t) => (t === 'theme-dark' ? 'theme-light' : 'theme-dark'))

  return { theme, toggle, isDark: theme === 'theme-dark' }
}
