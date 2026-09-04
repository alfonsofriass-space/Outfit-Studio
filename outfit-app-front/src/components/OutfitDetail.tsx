import { useEffect, useRef, useState } from 'react'
import { resolveImageUrl } from '../api/outfits'
import type {
  OutfitItem,
  OutfitUpdate,
  PersistedOutfit,
  PersistedOutfitImage,
  WornViewDetails,
} from '../types/outfit'
import {
  ProductSearchPanel,
  type ProductSearchAction,
} from './ProductSearchPanel'
import { ExpandableImage } from './ExpandableImage'

export interface WorkspaceWornViewAction {
  sourceImageId: number
  status: 'loading' | 'ready' | 'error'
  view?: WornViewDetails
  message?: string
}

interface OutfitDetailProps {
  outfit: PersistedOutfit
  openingOutfitId: number | null
  deletingOutfitId: number | null
  wornViewAction: WorkspaceWornViewAction | null
  productSearchAction: ProductSearchAction | null
  isPaidOperationLocked: boolean
  showTechnicalDetails: boolean
  isUpdating: boolean
  onUpdate: (outfitId: number, changes: OutfitUpdate) => void
  onBack: () => void
  onContinue: (outfitId: number) => void
  onDelete: (outfitId: number) => void
  onGenerateWornView: (outfitId: number, imageId: number) => void
  onSearchProduct: (
    outfitId: number,
    itemIndex: number,
    additionalDetails: string | null,
    forceNew: boolean,
  ) => void
}

const categoryLabels: Record<OutfitItem['category'], string> = {
  upper: 'Parte superior',
  lower: 'Parte inferior',
  one_piece: 'Prenda completa',
  footwear: 'Calzado',
  accessory: 'Accesorio',
}

const dateFormatter = new Intl.DateTimeFormat('es-ES', {
  day: 'numeric',
  month: 'long',
  year: 'numeric',
})

function compositionLabel(image: PersistedOutfitImage): string {
  return image.generation_number === 1
    ? 'Composición original'
    : `Variación ${image.generation_number - 1}`
}

function downloadName(
  outfitId: number,
  image: PersistedOutfitImage,
  kind: 'composicion' | 'vista-puesta',
): string {
  return `outfit-${outfitId}-${kind}-${image.generation_number}.png`
}

export function OutfitDetail({
  outfit,
  openingOutfitId,
  deletingOutfitId,
  wornViewAction,
  productSearchAction,
  isPaidOperationLocked,
  showTechnicalDetails,
  isUpdating,
  onUpdate,
  onBack,
  onContinue,
  onDelete,
  onGenerateWornView,
  onSearchProduct,
}: OutfitDetailProps) {
  const titleRef = useRef<HTMLHeadingElement>(null)
  const searchRef = useRef<HTMLDivElement>(null)
  const [selectedImageId, setSelectedImageId] = useState<number | null>(
    outfit.images.at(-1)?.image_id ?? null,
  )
  const selectedImage =
    outfit.images.find((image) => image.image_id === selectedImageId) ??
    outfit.images.at(-1) ??
    null
  const selectedAction =
    selectedImage && wornViewAction?.sourceImageId === selectedImage.image_id
      ? wornViewAction
      : null
  const wornView =
    selectedAction?.status === 'ready' && selectedAction.view
      ? selectedAction.view
      : selectedImage?.worn_view ?? null
  const isGeneratingWornView = selectedAction?.status === 'loading'
  const wornViewError =
    selectedAction?.status === 'error' ? selectedAction.message ?? null : null

  useEffect(() => {
    titleRef.current?.focus()
  }, [])

  const confirmDelete = () => {
    const confirmed = window.confirm(
      '¿Eliminar este outfit y todas sus imágenes guardadas? Esta acción no se puede deshacer.',
    )
    if (confirmed) onDelete(outfit.outfit_id)
  }

  const compositionCount = outfit.images.length
  const savedAt = dateFormatter.format(new Date(outfit.created_at))
  const isBusy =
    openingOutfitId !== null || isPaidOperationLocked || deletingOutfitId !== null

  return (
    <div
      className="archive-detail"
      role="region"
      aria-labelledby={`outfit-detail-title-${outfit.outfit_id}`}
    >
      <div className="archive-detail__bar">
        <button className="outfit-detail__back" type="button" onClick={onBack}>
          <span aria-hidden="true">←</span> Volver a la biblioteca
        </button>
        <button
          className={
            outfit.is_favourite
              ? 'archive-detail__favourite archive-detail__favourite--on'
              : 'archive-detail__favourite'
          }
          type="button"
          disabled={isUpdating}
          aria-pressed={outfit.is_favourite}
          onClick={() =>
            onUpdate(outfit.outfit_id, { is_favourite: !outfit.is_favourite })
          }
        >
          {outfit.is_favourite ? 'En favoritos' : 'Marcar favorito'}
        </button>
        <span className="archive-detail__stamp">
          {savedAt} ·{' '}
          {compositionCount === 1
            ? '1 composición'
            : `${compositionCount} composiciones`}
        </span>
      </div>

      <div className="archive-detail__grid">
        <div className="archive-detail__visual">
          {compositionCount === 0 ? (
            <div className="archive-pending">
              <p>
                El análisis está completo y no tiene ninguna composición. Puedes
                {showTechnicalDetails
                  ? ' retomarlo sin volver a llamar al modelo de texto.'
                  : ' retomarlo cuando quieras.'}
              </p>
              {showTechnicalDetails && (
                <span>
                  Coste estimado de la primera composición:{' '}
                  <strong>${outfit.flat_lay_estimated_cost.toFixed(3)}</strong>
                </span>
              )}
            </div>
          ) : (
            selectedImage && (
              <>
                <div className="archive-comparison">
                  <figure>
                    <ExpandableImage
                      className="archive-comparison__flat"
                      src={resolveImageUrl(selectedImage.image.url_or_base64)}
                      alt={`${compositionLabel(selectedImage)} del outfit: ${outfit.user_description}`}
                      label={compositionLabel(selectedImage)}
                    />
                    <figcaption>
                      <span>{compositionLabel(selectedImage)}</span>
                      <a
                        className="download-link"
                        href={resolveImageUrl(selectedImage.image.url_or_base64)}
                        download={downloadName(
                          outfit.outfit_id,
                          selectedImage,
                          'composicion',
                        )}
                      >
                        Descargar composición
                      </a>
                      <button
                        className="download-link"
                        type="button"
                        disabled={
                          isUpdating ||
                          outfit.chosen_image_id === selectedImage.image_id
                        }
                        aria-pressed={
                          outfit.chosen_image_id === selectedImage.image_id
                        }
                        onClick={() =>
                          onUpdate(outfit.outfit_id, {
                            chosen_image_id: selectedImage.image_id,
                          })
                        }
                      >
                        {outfit.chosen_image_id === selectedImage.image_id
                          ? 'Es la portada'
                          : 'Usar como portada'}
                      </button>
                    </figcaption>
                  </figure>

                  {wornView ? (
                    <figure>
                      <ExpandableImage
                        className="archive-comparison__worn"
                        src={resolveImageUrl(wornView.image.url_or_base64)}
                        alt={`Vista puesta del outfit: ${outfit.user_description}`}
                        label="Vista puesta"
                      />
                      <figcaption>
                        <span>Vista puesta</span>
                        <a
                          className="download-link"
                          href={resolveImageUrl(wornView.image.url_or_base64)}
                          download={downloadName(
                            outfit.outfit_id,
                            selectedImage,
                            'vista-puesta',
                          )}
                        >
                          Descargar vista puesta
                        </a>
                      </figcaption>
                    </figure>
                  ) : (
                    <div className="archive-worn-action">
                      <p className="eyebrow">Vista opcional</p>
                      <h3>Ver esta composición puesta</h3>
                      <p>
                        {showTechnicalDetails
                          ? 'Se usará este PNG como referencia. No se repetirá el análisis de texto.'
                          : 'Comprueba cómo podría quedar esta composición puesta.'}
                      </p>
                      {outfit.worn_view_preview ? (
                        <>
                          {showTechnicalDetails && (
                            <>
                              <details>
                                <summary>Ver prompt exacto</summary>
                                <pre>
                                  {outfit.worn_view_preview.generation_prompt}
                                </pre>
                              </details>
                              <p className="archive-worn-action__cost">
                                Coste aproximado:{' '}
                                <strong>
                                  ${outfit.worn_view_preview.estimated_cost.toFixed(3)}
                                </strong>
                              </p>
                            </>
                          )}
                          {wornViewError && (
                            <div className="notice notice--compact" role="alert">
                              <strong>{wornViewError}</strong>
                              <span>No se realizará un reintento automático.</span>
                            </div>
                          )}
                          {isGeneratingWornView && (
                            <div
                              className="generation-status generation-status--compact"
                              role="status"
                            >
                              <strong>Generando la vista puesta…</strong>
                              <span>Suele tardar entre 20 y 40 segundos.</span>
                            </div>
                          )}
                          <button
                            className="button button--primary"
                            type="button"
                            disabled={isBusy}
                            onClick={() =>
                              onGenerateWornView(
                                outfit.outfit_id,
                                selectedImage.image_id,
                              )
                            }
                          >
                            {isGeneratingWornView
                              ? 'Generando vista puesta…'
                              : wornViewError
                                ? 'Reintentar vista puesta'
                                : 'Generar vista puesta'}
                          </button>
                        </>
                      ) : (
                        <p className="muted">
                          Este outfit histórico no permite construir una vista
                          puesta segura.
                        </p>
                      )}
                    </div>
                  )}
                </div>

                {compositionCount > 1 && (
                  <div
                    className="composition-switcher"
                    aria-label="Composiciones del outfit"
                  >
                    {outfit.images.map((image) => (
                      <button
                        className={
                          selectedImage.image_id === image.image_id
                            ? 'composition-switcher__item composition-switcher__item--selected'
                            : 'composition-switcher__item'
                        }
                        type="button"
                        key={image.image_id}
                        disabled={deletingOutfitId !== null}
                        aria-pressed={selectedImage.image_id === image.image_id}
                        aria-label={`Mostrar ${compositionLabel(image).toLowerCase()}`}
                        onClick={() => setSelectedImageId(image.image_id)}
                      >
                        <img
                          src={resolveImageUrl(image.image.url_or_base64)}
                          alt=""
                        />
                        <span>{compositionLabel(image)}</span>
                        {image.worn_view && <small>Con vista puesta</small>}
                      </button>
                    ))}
                  </div>
                )}

                <div className="archive-image-meta">
                  {showTechnicalDetails && (
                    <>
                      <span>
                        Composición ${selectedImage.cost_estimate.toFixed(3)}
                      </span>
                      {wornView && (
                        <span>Vista ${wornView.cost_estimate.toFixed(3)}</span>
                      )}
                    </>
                  )}
                  <span>
                    {outfit.regenerations_remaining} variaciones disponibles
                  </span>
                </div>

                {showTechnicalDetails && (
                  <div className="archive-prompts">
                    <details>
                      <summary>Prompt de la composición</summary>
                      {selectedImage.generation_prompt ? (
                        <pre>{selectedImage.generation_prompt}</pre>
                      ) : (
                        <p className="muted">
                          No registrado en esta imagen histórica.
                        </p>
                      )}
                    </details>
                    {wornView && (
                      <details>
                        <summary>Prompt de la vista puesta</summary>
                        <pre>{wornView.generation_prompt}</pre>
                      </details>
                    )}
                  </div>
                )}
              </>
            )
          )}
        </div>

        <div className="archive-detail__aside">
          <div className="archive-detail__lead">
            <p className="eyebrow">
              {compositionCount === 0 ? 'Análisis guardado' : 'Outfit guardado'}
            </p>
            <h2
              id={`outfit-detail-title-${outfit.outfit_id}`}
              ref={titleRef}
              tabIndex={-1}
            >
              “{outfit.user_description}”
            </h2>
            <p>{outfit.outfit.outfit_summary}</p>
          </div>

          <div className="archive-garments">
            <div className="archive-garments__heading">
              <span className="section-heading__index" aria-hidden="true">
                04
              </span>
              <h3>Prendas interpretadas</h3>
            </div>
            <ul className="garment-rows">
              {outfit.outfit.items.map((item, index) => (
                <li key={`${item.category}-${index}`}>
                  <span>{item.item_type}</span>
                  <span>{categoryLabels[item.category]}</span>
                </li>
              ))}
            </ul>
            {outfit.accessories_omitted.length > 0 && (
              <p className="archive-garments__omitted">
                Fuera de la composición por el límite del board:{' '}
                {outfit.accessories_omitted.join(', ')}.
              </p>
            )}
          </div>

          <div className="archive-actions">
            {compositionCount > 0 && (
              <button
                className="archive-actions__search"
                type="button"
                onClick={() =>
                  searchRef.current?.scrollIntoView({
                    behavior: 'smooth',
                    block: 'start',
                  })
                }
              >
                <span>Buscar prendas similares</span>
                <svg
                  aria-hidden="true"
                  viewBox="0 0 20 20"
                  width="16"
                  height="16"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.3"
                  strokeLinecap="round"
                >
                  <circle cx="8.8" cy="8.8" r="6" />
                  <path d="M13.2 13.2 17.6 17.6" />
                </svg>
              </button>
            )}
            <button
              className="archive-actions__continue"
              type="button"
              disabled={isBusy}
              onClick={() => onContinue(outfit.outfit_id)}
            >
              <span>
                {openingOutfitId === outfit.outfit_id
                  ? 'Abriendo…'
                  : compositionCount === 0
                    ? 'Continuar análisis'
                    : 'Continuar en el generador'}
              </span>
              <svg
                aria-hidden="true"
                viewBox="0 0 20 8"
                width="18"
                height="8"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.2"
              >
                <path d="M0 4h18.4M15 .8 18.6 4 15 7.2" />
              </svg>
            </button>
            <div className="archive-actions__danger">
              <span>Esta acción no se puede deshacer</span>
              <button
                className="archive-actions__delete"
                type="button"
                disabled={isBusy}
                onClick={confirmDelete}
              >
                {deletingOutfitId === outfit.outfit_id
                  ? 'Eliminando outfit…'
                  : 'Eliminar outfit'}
              </button>
            </div>
          </div>
        </div>
      </div>

      {compositionCount > 0 && selectedImage && (
        <div ref={searchRef}>
          <ProductSearchPanel
            outfitId={outfit.outfit_id}
            items={outfit.outfit.items}
            searchItems={outfit.product_search_items}
            action={productSearchAction}
            isPaidOperationLocked={isPaidOperationLocked}
            showTechnicalDetails={showTechnicalDetails}
            onSearch={onSearchProduct}
          />
        </div>
      )}
    </div>
  )
}
