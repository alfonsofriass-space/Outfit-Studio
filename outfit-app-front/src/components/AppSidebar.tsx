import type { AuthenticatedUser } from '../api/auth'

export type AppSection = 'create' | 'library'

interface AppSidebarProps {
  activeSection: AppSection
  outfitCount: number
  operationLabel: string | null
  isNewOutfitDisabled: boolean
  currentUser: AuthenticatedUser
  sessionError: string | null
  onNavigate: (section: AppSection) => void
  onStartNew: () => void
  onLogout: () => void
}

function CreateIcon() {
  return (
    <svg
      aria-hidden="true"
      className="app-nav__icon"
      viewBox="0 0 20 20"
    >
      <path d="M10 2.5l.9 3.2a4.7 4.7 0 0 0 3.4 3.4l3.2.9-3.2.9a4.7 4.7 0 0 0-3.4 3.4l-.9 3.2-.9-3.2a4.7 4.7 0 0 0-3.4-3.4L2.5 10l3.2-.9a4.7 4.7 0 0 0 3.4-3.4L10 2.5Z" />
    </svg>
  )
}

function LibraryIcon() {
  return (
    <svg
      aria-hidden="true"
      className="app-nav__icon"
      viewBox="0 0 20 20"
    >
      <path d="M3.5 4.5c2.7-.7 4.8-.2 6.5 1.2 1.7-1.4 3.8-1.9 6.5-1.2v11c-2.7-.7-4.8-.2-6.5 1.2-1.7-1.4-3.8-1.9-6.5-1.2v-11Z" />
      <path d="M10 5.7v11" />
    </svg>
  )
}

export function AppSidebar({
  activeSection,
  outfitCount,
  operationLabel,
  isNewOutfitDisabled,
  currentUser,
  sessionError,
  onNavigate,
  onStartNew,
  onLogout,
}: AppSidebarProps) {
  return (
    <aside className="app-sidebar">
      <div className="app-sidebar__brand">
        <button
          className="brand"
          type="button"
          aria-label="Ir a crear outfit"
          onClick={() => onNavigate('create')}
        >
          <span className="brand__mark" aria-hidden="true">
            ✦
          </span>
          <span className="brand__copy">
            <strong>Outfit Studio</strong>
            <small>Estudio personal</small>
          </span>
        </button>
      </div>

      <nav className="app-nav" aria-label="Secciones principales">
        <button
          className="app-nav__button"
          type="button"
          aria-current={activeSection === 'create' ? 'page' : undefined}
          onClick={() => onNavigate('create')}
        >
          <CreateIcon />
          <span>Crear</span>
        </button>
        <button
          className="app-nav__button"
          type="button"
          aria-current={activeSection === 'library' ? 'page' : undefined}
          onClick={() => onNavigate('library')}
        >
          <LibraryIcon />
          <span>Biblioteca</span>
          <span className="app-nav__count" aria-hidden="true">
            {outfitCount}
          </span>
        </button>
      </nav>

      <div className="app-sidebar__footer">
        {operationLabel && (
          <span className="operation-indicator" role="status">
            {operationLabel}
          </span>
        )}
        <button
          className="button sidebar-new-outfit"
          type="button"
          disabled={isNewOutfitDisabled}
          onClick={onStartNew}
        >
          <svg aria-hidden="true" viewBox="0 0 10 10" width="10" height="10" fill="none" stroke="currentColor" stroke-width="1.2"><path d="M5 .6v8.8M.6 5h8.8" /></svg>
          Nuevo outfit
        </button>
        <div className="sidebar-account">
          <span className="sidebar-account__avatar" aria-hidden="true">
            {currentUser.username.charAt(0).toUpperCase()}
          </span>
          <span className="sidebar-account__copy">
            <strong>{currentUser.username}</strong>
            <small>{currentUser.role === 'admin' ? 'Administrador' : 'Cuenta personal'}</small>
          </span>
        </div>
        {sessionError && (
          <span className="sidebar-account__error" role="alert">
            {sessionError}
          </span>
        )}
        <button
          className="app-sidebar__logout"
          type="button"
          onClick={onLogout}
        >
          Cerrar sesión
        </button>
      </div>
    </aside>
  )
}
