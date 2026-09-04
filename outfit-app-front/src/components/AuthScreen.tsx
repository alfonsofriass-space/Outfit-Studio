import { useState, type FormEvent } from 'react'

export type AuthMode = 'login' | 'register'

interface AuthScreenProps {
  isSubmitting: boolean
  error: string | null
  onSubmit: (mode: AuthMode, username: string, password: string) => void
}

export function AuthScreen({ isSubmitting, error, onSubmit }: AuthScreenProps) {
  const [mode, setMode] = useState<AuthMode>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const normalizedUsername = username.trim()
    if (!normalizedUsername || !password) return
    onSubmit(mode, normalizedUsername, password)
  }

  const selectMode = (nextMode: AuthMode) => {
    if (isSubmitting) return
    setMode(nextMode)
  }

  return (
    <main className="auth-shell">
      <section className="auth-intro" aria-labelledby="auth-title">
        <a className="auth-brand" href="#auth-title" aria-label="Outfit Studio">
          <span className="brand__mark" aria-hidden="true">
            ✦
          </span>
          <span className="brand__copy">
            <strong>Outfit Studio</strong>
            <small>Tu armario visual</small>
          </span>
        </a>
        <div>
          <p className="eyebrow">Un espacio solo para tus ideas</p>
          <h1 id="auth-title">Imagina, guarda y vuelve a tus looks.</h1>
          <p className="auth-intro__copy">
            Describe una idea, comprueba sus prendas y conserva cada composición en
            tu biblioteca personal.
          </p>
        </div>
        <p className="auth-intro__note">
          <span>Ninguna imagen se genera sin tu confirmación</span>
          <span>{new Date().getFullYear()}</span>
        </p>
      </section>

      <section className="auth-panel" aria-labelledby="auth-form-title">
        <div className="auth-tabs" role="tablist" aria-label="Acceso a la cuenta">
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'login'}
            disabled={isSubmitting}
            onClick={() => selectMode('login')}
          >
            Iniciar sesión
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={mode === 'register'}
            disabled={isSubmitting}
            onClick={() => selectMode('register')}
          >
            Crear cuenta
          </button>
        </div>

        <div className="auth-panel__heading">
          <p className="eyebrow">
            {mode === 'login' ? 'Bienvenido de nuevo' : 'Tu espacio personal'}
          </p>
          <h2 id="auth-form-title">
            {mode === 'login' ? 'Entra en tu biblioteca.' : 'Crea tu cuenta.'}
          </h2>
          <p>
            {mode === 'login'
              ? 'Continúa con tus outfits guardados.'
              : 'Solo necesitas un nombre de usuario y una contraseña.'}
          </p>
        </div>

        {error && (
          <div className="notice notice--compact" role="alert">
            <strong>{error}</strong>
          </div>
        )}

        <form className="auth-form" onSubmit={submit}>
          <label>
            <span>Nombre de usuario</span>
            <input
              name="username"
              type="text"
              minLength={3}
              maxLength={32}
              pattern="[A-Za-z0-9._-]+"
              autoComplete={mode === 'login' ? 'username' : 'new-username'}
              disabled={isSubmitting}
              required
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
            {mode === 'register' && (
              <small>Letras, números, punto, guion o guion bajo.</small>
            )}
          </label>

          <label>
            <span>Contraseña</span>
            <input
              name="password"
              type="password"
              minLength={4}
              maxLength={128}
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              disabled={isSubmitting}
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
            {mode === 'register' && <small>Mínimo cuatro caracteres.</small>}
          </label>

          <button className="button button--primary auth-form__submit" disabled={isSubmitting}>
            {isSubmitting
              ? mode === 'login'
                ? 'Entrando…'
                : 'Creando cuenta…'
              : mode === 'login'
                ? 'Entrar en el estudio'
                : 'Crear mi cuenta'}
            <svg aria-hidden="true" viewBox="0 0 20 8" width="20" height="8" fill="none" stroke="currentColor" strokeWidth="1.2"><path d="M0 4h18.4M15 .8 18.6 4 15 7.2" /></svg>
          </button>
        </form>

        <p className="auth-panel__switch">
          {mode === 'login' ? '¿Todavía no tienes cuenta?' : '¿Ya tienes una cuenta?'}{' '}
          <button
            className="text-button"
            type="button"
            disabled={isSubmitting}
            onClick={() => selectMode(mode === 'login' ? 'register' : 'login')}
          >
            {mode === 'login' ? 'Crear cuenta' : 'Iniciar sesión'}
          </button>
        </p>
      </section>
    </main>
  )
}
