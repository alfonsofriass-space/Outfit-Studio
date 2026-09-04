import { useEffect, useState } from 'react'
import { resolveImageUrl } from '../api/outfits'
import type { OutfitUpdate, PersistedOutfit } from '../types/outfit'
import {
  OutfitDetail,
  type WorkspaceWornViewAction,
} from './OutfitDetail'
import type { ProductSearchAction } from './ProductSearchPanel'

interface OutfitWorkspaceProps {
  outfits: PersistedOutfit[]
  isLoading: boolean
  openingOutfitId: number | null
  deletingOutfitId: number | null
  wornViewAction: WorkspaceWornViewAction | null
  productSearchAction: ProductSearchAction | null
  isPaidOperationLocked: boolean
  showTechnicalDetails: boolean
  error: string | null
  hasMoreOutfits: boolean
  isLoadingMore: boolean
  favouritesOnly: boolean
  updatingOutfitId: number | null
  onLoadMore: () => void
  onToggleFavouritesOnly: (value: boolean) => void
  onUpdateOutfit: (outfitId: number, changes: OutfitUpdate) => void
  onContinue: (outfitId: number) => void
  onDelete: (outfitId: number) => void
  onGenerateWornView: (outfitId: number, imageId: number) => void
  onSearchProduct: (
    outfitId: number,
    itemIndex: number,
    additionalDetails: string | null,
    forceNew: boolean,
  ) => void
  onRetry: () => void
}

// La portada es la composición que el usuario eligió. Sin elección se usa la última
// y no la primera: la original es muchas veces justo la que descartó al regenerar.
function coverImageOf(outfit: PersistedOutfit) {
  const chosen = outfit.images.find(
    (image) => image.image_id === outfit.chosen_image_id,
  )
  return chosen ?? outfit.images.at(-1) ?? null
}

function formatCreatedAt(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''

  return new Intl.DateTimeFormat('es-ES', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  }).format(date)
}

export function OutfitWorkspace({
  outfits,
  isLoading,
  openingOutfitId,
  deletingOutfitId,
  wornViewAction,
  productSearchAction,
  isPaidOperationLocked,
  showTechnicalDetails,
  error,
  hasMoreOutfits,
  isLoadingMore,
  favouritesOnly,
  updatingOutfitId,
  onLoadMore,
  onToggleFavouritesOnly,
  onUpdateOutfit,
  onContinue,
  onDelete,
  onGenerateWornView,
  onSearchProduct,
  onRetry,
}: OutfitWorkspaceProps) {
  const [selectedOutfitId, setSelectedOutfitId] = useState<number | null>(null)
  const [returnFocusOutfitId, setReturnFocusOutfitId] = useState<number | null>(
    null,
  )
  const selectedOutfit =
    outfits.find((outfit) => outfit.outfit_id === selectedOutfitId) ?? null

  useEffect(() => {
    if (selectedOutfitId !== null || returnFocusOutfitId === null) return

    document.getElementById(`outfit-card-${returnFocusOutfitId}`)?.focus()
    setReturnFocusOutfitId(null)
  }, [returnFocusOutfitId, selectedOutfitId])

  const closeDetail = () => {
    setReturnFocusOutfitId(selectedOutfitId)
    setSelectedOutfitId(null)
  }

  if (selectedOutfit) {
    return (
      <section className="archive-section" aria-label="Detalle del outfit">
        <OutfitDetail
          key={selectedOutfit.outfit_id}
          outfit={selectedOutfit}
          openingOutfitId={openingOutfitId}
          deletingOutfitId={deletingOutfitId}
          wornViewAction={wornViewAction}
          productSearchAction={productSearchAction}
          isPaidOperationLocked={isPaidOperationLocked}
          showTechnicalDetails={showTechnicalDetails}
          isUpdating={updatingOutfitId === selectedOutfit.outfit_id}
          onUpdate={onUpdateOutfit}
          onBack={closeDetail}
          onContinue={onContinue}
          onDelete={onDelete}
          onGenerateWornView={onGenerateWornView}
          onSearchProduct={onSearchProduct}
        />
      </section>
    )
  }

  return (
    <section
      className="archive-section"
      aria-labelledby="archive-title"
      aria-busy={isLoading}
    >
      <div className="library-heading">
        <div className="library-heading__lead">
          <p className="eyebrow">Tu colección personal</p>
          <h2 id="archive-title" tabIndex={-1}>
            Biblioteca de <em>looks.</em>
          </h2>
          <p className="library-heading__intro">
            Recupera una idea, compara sus composiciones o busca prendas
            similares cuando quieras.
          </p>
        </div>
        {outfits.length > 0 && (
          <p className="archive-count">
            <strong>{outfits.length}</strong>
            <span>
              {outfits.length === 1 ? 'outfit guardado' : 'outfits guardados'}
            </span>
          </p>
        )}
      </div>

      {(outfits.length > 0 || favouritesOnly) && (
        <div className="archive-filter">
          <button
            className={favouritesOnly ? 'archive-filter__option' : 'archive-filter__option archive-filter__option--active'}
            type="button"
            aria-pressed={!favouritesOnly}
            onClick={() => onToggleFavouritesOnly(false)}
          >
            Todos
          </button>
          <button
            className={favouritesOnly ? 'archive-filter__option archive-filter__option--active' : 'archive-filter__option'}
            type="button"
            aria-pressed={favouritesOnly}
            onClick={() => onToggleFavouritesOnly(true)}
          >
            Favoritos
          </button>
        </div>
      )}

      {isLoading && outfits.length === 0 && (
        <p className="muted">Cargando outfits…</p>
      )}

      {error && outfits.length === 0 && (
        <div className="empty-state">
          <p>{error}</p>
          <button className="text-button" type="button" onClick={onRetry}>
            Volver a intentar
          </button>
        </div>
      )}

      {!isLoading && !error && !favouritesOnly && outfits.length === 0 && (
        <div className="empty-state">
          <p>Los análisis y composiciones que guardes aparecerán aquí.</p>
        </div>
      )}

      {outfits.length > 0 && (
        <div className="outfit-card-grid" aria-label="Outfits guardados">
          {outfits.map((outfit) => {
            const imageCount = outfit.images.length
            const coverImage = coverImageOf(outfit)
            const createdAt = formatCreatedAt(outfit.created_at)

            return (
              <button
                className="outfit-card"
                type="button"
                key={outfit.outfit_id}
                id={`outfit-card-${outfit.outfit_id}`}
                disabled={deletingOutfitId !== null}
                onClick={() => setSelectedOutfitId(outfit.outfit_id)}
              >
                <span className="outfit-card__visual">
                  {coverImage ? (
                    <img
                      src={resolveImageUrl(coverImage.image.url_or_base64)}
                      alt={`Portada del outfit: ${outfit.user_description}`}
                      loading="lazy"
                      decoding="async"
                    />
                  ) : (
                    <span className="outfit-card__placeholder">
                      <svg aria-hidden="true" viewBox="0 0 10 10" width="10" height="10" fill="none" stroke="currentColor" stroke-width="1.2"><path d="M5 .6v8.8M.6 5h8.8" /></svg>
                      Sin composición
                    </span>
                  )}
                  <span className="outfit-card__badge">
                    {imageCount === 0
                      ? 'Análisis guardado'
                      : `${imageCount} ${
                          imageCount === 1 ? 'composición' : 'composiciones'
                        }`}
                  </span>
                </span>
                <span className="outfit-card__content">
                  <span className="outfit-card__meta">
                    {createdAt && (
                      <span className="outfit-card__date">{createdAt}</span>
                    )}
                    {outfit.is_favourite && (
                      <span className="outfit-card__favourite">Favorito</span>
                    )}
                  </span>
                  <strong>“{outfit.user_description}”</strong>
                  <small>{outfit.outfit.outfit_summary}</small>
                  <span className="outfit-card__action">
                    {imageCount === 0 ? 'Abrir análisis' : 'Ver outfit'}
                    <span aria-hidden="true">→</span>
                  </span>
                </span>
              </button>
            )
          })}
        </div>
      )}

      {hasMoreOutfits && (
        <div className="archive-more">
          <button
            className="button button--secondary"
            type="button"
            disabled={isLoadingMore}
            onClick={onLoadMore}
          >
            {isLoadingMore ? 'Cargando…' : 'Cargar más'}
          </button>
        </div>
      )}

      {!isLoading && !error && favouritesOnly && outfits.length === 0 && (
        <div className="empty-state">
          <p>Todavía no has marcado ningún outfit como favorito.</p>
        </div>
      )}

      {error && outfits.length > 0 && (
        <div
          className="notice notice--compact archive-refresh-error"
          role="alert"
        >
          <strong>{error}</strong>
          <button className="text-button" type="button" onClick={onRetry}>
            Actualizar biblioteca
          </button>
        </div>
      )}
    </section>
  )
}
