import { useEffect } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import useAuthStore from './store/authStore'
import useTheme from './hooks/useTheme'
import Login from './pages/Login'
import Board from './pages/Board'

function PrivateRoute({ children }) {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  return isAuthenticated ? children : <Navigate to="/login" replace />
}

export default function App() {
  const restoreToken = useAuthStore((s) => s.restoreToken)
  useTheme()   // applies the persisted theme class to <body>

  useEffect(() => {
    restoreToken()
  }, [restoreToken])

  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/board"
        element={
          <PrivateRoute>
            <Board />
          </PrivateRoute>
        }
      />
      <Route path="*" element={<Navigate to="/board" replace />} />
    </Routes>
  )
}
