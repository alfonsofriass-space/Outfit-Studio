import { useState } from 'react'
import type {
  OutfitItem,
  ProductSearchItemState,
} from '../types/outfit'

export interface ProductSearchAction {
  outfitId: number
  itemIndex: number
  status: 'loading' | 'error'
  message?: string
}

function formatSearchedAt(value: string): string | null {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return null

  return new Intl.DateTimeFormat('es-ES', {
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  }).format(date)
}

interface ProductSearchPanelProps {
  outfitId: number
  items: OutfitItem[]
  searchItems: ProductSearchItemState[]
  action: ProductSearchAction | null
  isPaidOperationLocked: boolean
  showTechnicalDetails: boolean
  onSearch: (
    outfitId: number,
    itemIndex: number,
    additionalDetails: string | null,
    forceNew: boolean,
  ) => void
}

function itemAttributes(item: OutfitItem): string[] {
  return [
    item.brand,
    item.color,
    item.material,
    item.fit,
    ...item.details,
  ].filter((value): value is string => Boolean(value))
}

export function ProductSearchPanel({
  outfitId,
  items,
  searchItems,
  action,
  isPaidOperationLocked,
  showTechnicalDetails,
  onSearch,
}: ProductSearchPanelProps) {
  const [detailsByItem, setDetailsByItem] = useState<Record<number, string>>({})

  return (
    <section className="product-search" aria-labelledby={`product-search-${outfitId}`}>
      <div className="product-search__heading">
        <span className="section-heading__index" aria-hidden="true">
          03
        </span>
        <div>
          <h3 id={`product-search-${outfitId}`}>Prendas encontradas</h3>
          <span>Hasta 3 resultados por prenda</span>
        </div>
      </div>
      <p className="product-search__intro">
        {showTechnicalDetails
          ? 'La búsqueda usa los datos interpretados y todo lo que añadas en el campo, incluida una marca concreta. Una marca explícita se conserva como requisito; no compara visualmente la composición ni confirma disponibilidad.'
          : 'Añade en el mismo campo una marca, color, material o cualquier detalle importante para afinar la búsqueda.'}
      </p>

      <div className="product-search__items">
        {searchItems.map((state) => {
          const item = items[state.item_index]
          if (!item) return null

          const itemAction =
            action?.outfitId === outfitId && action.itemIndex === state.item_index
              ? action
              : null
          const isLoading = itemAction?.status === 'loading'
          const error = itemAction?.status === 'error' ? itemAction.message : null
          const additionalDetails = detailsByItem[state.item_index] ?? ''
          const attributes = itemAttributes(item)
          const hasAttemptsLeft = state.attempts_remaining > 0
          const searchedAt = state.search
            ? formatSearchedAt(state.search.created_at)
            : null

          return (
            <article className="product-search__item" key={state.item_index}>
              <div className="product-search__item-heading">
                <span className="section-heading__index" aria-hidden="true">
                  {`0${state.item_index + 1}`}
                </span>
                <div>
                  <h4>{item.item_type}</h4>
                  {state.search ? (
                    <span className="status-pill status-pill--success">
                      Búsqueda guardada
                    </span>
                  ) : (
                    <span className="product-search__remaining">
                      {state.attempts_remaining === 1
                        ? '1 búsqueda restante'
                        : `${state.attempts_remaining} búsquedas restantes`}
                    </span>
                  )}
                </div>
              </div>

              {attributes.length > 0 && (
                <ul className="tag-list" aria-label={`Detalles de ${item.item_type}`}>
                  {attributes.map((attribute, index) => (
                    <li key={`${attribute}-${index}`}>{attribute}</li>
                  ))}
                </ul>
              )}

              {state.search && (
                <div className="product-search__result">
                  {showTechnicalDetails && (
                    <p className="product-search__query">
                      Consulta guardada: <code>{state.search.query}</code>
                    </p>
                  )}
                  {state.search.candidates.length === 0 ? (
                    <div className="product-search__empty">
                      No encontramos productos verificables en esta búsqueda. No se
                      repetirá sola: puedes añadir un detalle y buscar de nuevo.
                    </div>
                  ) : (
                    <div className="product-candidates">
                      {state.search.candidates.map((candidate) => (
                        <article
                          className="product-candidate"
                          key={candidate.product_url}
                        >
                          <div className="product-candidate__content">
                            <span>{candidate.store}</span>
                            <strong>{candidate.title}</strong>
                            {candidate.price_text ? (
                              <small>{candidate.price_text}</small>
                            ) : (
                              <small className="product-candidate__no-price">
                                Precio no indicado
                              </small>
                            )}
                            <a
                              href={candidate.product_url}
                              target="_blank"
                              rel="noopener noreferrer"
                            >
                              Abrir producto
                            </a>
                          </div>
                        </article>
                      ))}
                    </div>
                  )}
                  {searchedAt && (
                    <p className="product-search__searched-at">
                      Buscado el {searchedAt}. Los enlaces de tienda pueden haber
                      caducado o cambiado de precio desde entonces.
                    </p>
                  )}
                  {showTechnicalDetails && (
                    <p className="product-search__measured-cost">
                      Coste calculado de esta búsqueda:{' '}
                      <strong>${state.search.cost_estimate.toFixed(3)}</strong>
                    </p>
                  )}
                </div>
              )}

              {hasAttemptsLeft ? (
                <div className="product-search__action">
                  {showTechnicalDetails && state.query && !state.search ? (
                    <p className="product-search__query">
                      Consulta base: <code>{state.query}</code>
                    </p>
                  ) : state.needs_details ? (
                    <p className="product-search__missing">{state.message}</p>
                  ) : null}
                  <div className="product-search__field">
                    <label htmlFor={`product-details-${outfitId}-${state.item_index}`}>
                      {state.needs_details
                        ? 'Añade el tipo, marca, color, material, corte o detalle que falta'
                        : state.search
                          ? 'Añade o cambia un detalle y vuelve a buscar (opcional)'
                          : 'Añade un detalle para afinar la búsqueda (opcional)'}
                    </label>
                    <input
                      id={`product-details-${outfitId}-${state.item_index}`}
                      type="text"
                      maxLength={200}
                      value={additionalDetails}
                      disabled={isPaidOperationLocked}
                      placeholder={
                        state.needs_details
                          ? 'Ej.: bolso Versace pequeño de piel verde'
                          : 'Ej.: Versace, verde militar, manga corta o acabado satinado'
                      }
                      onChange={(event) =>
                        setDetailsByItem((current) => ({
                          ...current,
                          [state.item_index]: event.target.value,
                        }))
                      }
                    />
                  </div>

                  {error && (
                    <div className="notice notice--compact" role="alert">
                      <strong>{error}</strong>
                    </div>
                  )}
                  {isLoading && (
                    <div
                      className="generation-status generation-status--compact"
                      role="status"
                    >
                      <strong>Buscando productos…</strong>
                      <span>La composición y tus resultados guardados siguen visibles.</span>
                    </div>
                  )}
                  <button
                    className="button button--primary"
                    type="button"
                    disabled={
                      isPaidOperationLocked ||
                      (state.needs_details && !additionalDetails.trim())
                    }
                    onClick={() =>
                      onSearch(
                        outfitId,
                        state.item_index,
                        additionalDetails.trim() || null,
                        // Una búsqueda previa solo se repite por acción explícita.
                        state.search !== null,
                      )
                    }
                  >
                    {isLoading
                      ? 'Buscando productos…'
                      : `${
                          error
                            ? 'Reintentar búsqueda'
                            : state.search
                              ? 'Buscar de nuevo'
                              : 'Buscar similares'
                        }${
                          showTechnicalDetails
                            ? ` · ≈ $${state.estimated_cost.toFixed(2)}`
                            : ''
                        }`}
                  </button>
                  {state.search && (
                    <p className="product-search__attempts">
                      {state.attempts_remaining === 1
                        ? 'Te queda 1 búsqueda para esta prenda.'
                        : `Te quedan ${state.attempts_remaining} búsquedas para esta prenda.`}
                    </p>
                  )}
                </div>
              ) : (
                <p className="product-search__attempts">
                  Has agotado las búsquedas disponibles para esta prenda.
                </p>
              )}
            </article>
          )
        })}
      </div>
    </section>
  )
}
