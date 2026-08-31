import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import axios from 'axios'

const useAuthStore = create(persist(
  (set, get) => ({
    user: null,
    token: null,
    isAuthenticated: false,

    login: async (username, password) => {
      const params = new URLSearchParams({ username, password })
      const resp = await axios.post('/api/auth/login', params)
      const { access_token, role, name, state_codes } = resp.data
      axios.defaults.headers.common['Authorization'] = `Bearer ${access_token}`
      set({
        token: access_token,
        user: { username, role, name, stateCodes: state_codes || [] },
        isAuthenticated: true,
      })
      return role
    },

    logout: () => {
      delete axios.defaults.headers.common['Authorization']
      set({ user: null, token: null, isAuthenticated: false })
    },

    restoreToken: () => {
      const { token } = get()
      if (token) axios.defaults.headers.common['Authorization'] = `Bearer ${token}`
    },
  }),
  {
    name: 'nusatel-auth',
    partialize: (s) => ({ user: s.user, token: s.token, isAuthenticated: s.isAuthenticated }),
  },
))

export default useAuthStore
