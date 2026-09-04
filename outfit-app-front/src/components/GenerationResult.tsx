import { resolveImageUrl } from '../api/outfits'
import type { GeneratedImage, OutfitAnalysis, WornViewDetails } from '../types/outfit'
import { ExpandableImage } from './ExpandableImage'
import {
  ProductSearchPanel,
  type ProductSearchAction,
} from './ProductSearchPanel'

interface GenerationResultProps {
  analysis: OutfitAnalysis
  generation: GeneratedImage
  isGeneratingVariation: boolean
  variationError: string | null
  wornView: WornViewDetails | null
  isGeneratingWornView: boolean
  isImageOperationLocked: boolean
  showTechnicalDetails: boolean
  wornViewError: string | null
  productSearchAction: ProductSearchAction | null
  onGenerateWornView: () => void
  onGenerateVariation: () => void
  onStartAgain: () => void
  onSearchProduct: (
    outfitId: number,
    itemIndex: number,
    additionalDetails: string | null,
    forceNew: boolean,
  ) => void
}

export function GenerationResult({
  analysis,
  generation,
  isGeneratingVariation,
  variationError,
  wornView,
  isGeneratingWornView,
  isImageOperationLocked,
  showTechnicalDetails,
  wornViewError,
  productSearchAction,
  onGenerateWornView,
  onGenerateVariation,
  onStartAgain,
  onSearchProduct,
}: GenerationResultProps) {
  const isBusy =
    isGeneratingVariation || isGeneratingWornView || isImageOperationLocked

  return (
    <section className="result" aria-labelledby="create-view-title">

      <div className={`result__images ${wornView ? 'result__images--paired' : ''}`}>
          <figure>
            <ExpandableImage
              className="result__image result__image--flat-lay"
              src={resolveImageUrl(generation.image.url_or_base64)}
              alt={`Outfit generado a partir de: ${analysis.user_description}`}
              label="Composición"
            />
            <figcaption>Composición</figcaption>
          </figure>
          {wornView && (
            <figure>
              <ExpandableImage
                className="result__image result__image--worn"
                src={resolveImageUrl(wornView.image.url_or_base64)}
                alt={`Vista puesta del outfit: ${analysis.user_description}`}
                label="Vista puesta"
              />
              <figcaption>Vista puesta</figcaption>
            </figure>
          )}
      </div>

      <div className="result__layout">
        <div className="result__details">
          <p className="eyebrow">Tu descripción</p>
          <p className="quote">“{analysis.user_description}”</p>
          <ul className="tag-list result__pieces">
            {analysis.outfit.items.map((item, index) => (
              <li key={`${item.category}-${index}`}>{item.item_type}</li>
            ))}
          </ul>
          {showTechnicalDetails && (
            <>
              <details>
                <summary>Ver prompt exacto de esta imagen</summary>
                {generation.generation_prompt ? (
                  <pre>{generation.generation_prompt}</pre>
                ) : (
                  <p className="muted">No registrado en esta imagen histórica.</p>
                )}
              </details>
              <dl className="image-meta">
                <div>
                  <dt>Modelo</dt>
                  <dd>{generation.image.model}</dd>
                </div>
                <div>
                  <dt>Formato</dt>
                  <dd>{generation.image.size}</dd>
                </div>
              </dl>
            </>
          )}
        </div>

        {generation.worn_view_preview && (
            <section className="worn-view-control" aria-labelledby="worn-view-title">
              <p className="eyebrow">Vista opcional</p>
              <h3 id="worn-view-title">Ver el outfit puesto</h3>
              <p>
                {showTechnicalDetails
                  ? 'Se utilizará esta composición como referencia visual para vestir un maniquí neutro. No se volverá a analizar tu texto.'
                  : 'Crea una vista adicional para comprobar cómo quedaría puesto.'}
              </p>
              {showTechnicalDetails && (
                <>
                  <details>
                    <summary>Ver prompt exacto de la vista puesta</summary>
                    <pre>{generation.worn_view_preview.generation_prompt}</pre>
                  </details>
                  <p className="worn-view-control__cost">
                    Coste habitual aproximado:{' '}
                    <strong>${generation.worn_view_preview.estimated_cost.toFixed(3)}</strong>
                  </p>
                </>
              )}
              {wornViewError && (
                <div className="notice notice--compact" role="alert">
                  <strong>{wornViewError}</strong>
                  <span>
                    La composición sigue guardada y no se realizará un reintento automático.
                  </span>
                </div>
              )}
              {isGeneratingWornView && (
                <div className="generation-status generation-status--compact" role="status">
                  <strong>Generando la vista puesta…</strong>
                  <span>Suele tardar entre 20 y 40 segundos.</span>
                </div>
              )}
              {wornView ? (
                <span className="status-pill status-pill--success">
                  Vista puesta guardada
                </span>
              ) : (
                <button
                  className="button button--primary worn-view-control__button"
                  type="button"
                  disabled={isBusy}
                  onClick={onGenerateWornView}
                >
                  {isGeneratingWornView
                    ? 'Generando vista puesta…'
                    : wornViewError
                      ? showTechnicalDetails
                        ? `Reintentar vista puesta · ≈ $${generation.worn_view_preview.estimated_cost.toFixed(3)}`
                        : 'Reintentar vista puesta'
                      : showTechnicalDetails
                        ? `Generar vista puesta · ≈ $${generation.worn_view_preview.estimated_cost.toFixed(3)}`
                        : 'Generar vista puesta'}
                </button>
              )}
          </section>
        )}
      </div>

      {isGeneratingVariation && (
        <div className="generation-status" role="status">
          <strong>Generando otra composición…</strong>
          <span>
            Suele tardar entre 20 y 40 segundos. La composición actual seguirá
            visible mientras esperas.
          </span>
        </div>
      )}

      {variationError && (
        <div className="notice notice--compact result__retry-notice" role="alert">
          <strong>{variationError}</strong>
          <span>
            La composición actual sigue guardada y puedes reintentar manualmente.
          </span>
        </div>
      )}

      {analysis.product_search_items.length > 0 && (
        <ProductSearchPanel
          outfitId={analysis.outfit_id}
          items={analysis.outfit.items}
          searchItems={analysis.product_search_items}
          action={productSearchAction}
          isPaidOperationLocked={isImageOperationLocked}
          showTechnicalDetails={showTechnicalDetails}
          onSearch={onSearchProduct}
        />
      )}

      <div className="review__actions">
        <button
          className="button button--secondary"
          type="button"
          disabled={isBusy}
          onClick={onStartAgain}
        >
          Crear otro outfit
        </button>
        {analysis.image_prompt && generation.regenerations_remaining > 0 && (
          <button
            className="button button--primary"
            type="button"
            disabled={isBusy}
            onClick={onGenerateVariation}
          >
            {isGeneratingVariation
              ? 'Generando composición…'
              : variationError
                ? 'Reintentar variación'
                : 'Generar otra composición'}
          </button>
        )}
      </div>
    </section>
  )
}
