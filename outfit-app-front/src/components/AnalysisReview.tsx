import type { OutfitAnalysis, OutfitItem } from '../types/outfit'

const categoryLabels: Record<OutfitItem['category'], string> = {
  upper: 'Parte superior',
  lower: 'Parte inferior',
  one_piece: 'Prenda completa',
  footwear: 'Calzado',
  accessory: 'Accesorio',
}

const certaintyLabels: Record<OutfitItem['certainty'], string> = {
  high: 'Alta',
  medium: 'Media',
  low: 'Baja',
}

function itemAttributes(item: OutfitItem): string[] {
  return [item.brand, item.color, item.material, item.fit, ...item.details].filter(
    (value): value is string => Boolean(value),
  )
}

interface AnalysisReviewProps {
  analysis: OutfitAnalysis
  isGenerating: boolean
  isImageOperationLocked: boolean
  isRetry: boolean
  showTechnicalDetails: boolean
  onEdit: () => void
  onGenerate: () => void
}

export function AnalysisReview({
  analysis,
  isGenerating,
  isImageOperationLocked,
  isRetry,
  showTechnicalDetails,
  onEdit,
  onGenerate,
}: AnalysisReviewProps) {
  return (
    <section className="review" aria-labelledby="review-title">
      <div className="review__description">
        <p className="eyebrow">Tu descripción</p>
        <p>“{analysis.user_description}”</p>
      </div>

      <div className="section-heading">
        <span className="section-heading__index" aria-hidden="true">
          02
        </span>
        <div>
          <h2 id="review-title">Prendas interpretadas</h2>
          <span>
            {analysis.outfit.items.length === 1
              ? 'una prenda'
              : `${analysis.outfit.items.length} prendas`}
          </span>
        </div>
      </div>

      <div className="garment-grid">
        {analysis.outfit.items.map((item, index) => {
          const attributes = itemAttributes(item)
          return (
            <article className="garment-card" key={`${item.category}-${index}`}>
              <div className="garment-card__topline">
                <span>{categoryLabels[item.category]}</span>
                <span>
                  {showTechnicalDetails
                    ? `Certeza ${certaintyLabels[item.certainty].toLowerCase()}`
                    : `0${index + 1}`}
                </span>
              </div>
              <h3>{item.item_type}</h3>
              {attributes.length > 0 ? (
                <ul className="tag-list" aria-label={`Detalles de ${item.item_type}`}>
                  {attributes.map((attribute) => (
                    <li key={attribute}>{attribute}</li>
                  ))}
                </ul>
              ) : (
                <p className="muted">Sin detalles adicionales inferidos.</p>
              )}
            </article>
          )
        })}
      </div>

      {showTechnicalDetails && analysis.outfit.styling_notes_en.length > 0 && (
        <div className="notice review__styling-notes">
          <strong>Relaciones explícitas interpretadas</strong>
          <span>Estas instrucciones pueden cambiar la colocación normal de las prendas:</span>
          <ul>
            {analysis.outfit.styling_notes_en.map((note, index) => (
              <li key={`${index}-${note}`}>{note}</li>
            ))}
          </ul>
        </div>
      )}

      {analysis.accessories_omitted.length > 0 && (
        <div className="notice notice--warning">
          <strong>Accesorios fuera de la composición:</strong>{' '}
          {analysis.accessories_omitted.join(', ')}.
        </div>
      )}

      {showTechnicalDetails && (
        <details className="prompt-panel">
          <summary>Ver prompt técnico exacto</summary>
          <div className="prompt-panel__content">
            <div>
              <p className="eyebrow">Entrada exacta del modelo visual</p>
              <h3>Esto es lo que verá el agente de imagen</h3>
            </div>
            {analysis.image_prompt ? (
              <pre>{analysis.image_prompt}</pre>
            ) : (
              <p>Este análisis histórico no conserva un prompt visual.</p>
            )}
          </div>
        </details>
      )}

      {!analysis.image_prompt && (
        <div className="notice notice--warning">
          <strong>No se puede generar desde este análisis histórico.</strong>
          <span>
            {showTechnicalDetails
              ? 'El prompt visual original no quedó registrado.'
              : 'Crea de nuevo esta idea para preparar una composición.'}
          </span>
        </div>
      )}

      {isGenerating && (
        <div className="generation-status" role="status">
          <strong>Generando la composición…</strong>
          <span>
            Suele tardar entre 20 y 40 segundos. Espera a que termine antes de
            volver a pulsar.
          </span>
        </div>
      )}

      <div className="review__actions">
        <button className="button button--secondary" type="button" onClick={onEdit}>
          Editar descripción
        </button>
        <button
          className="button button--primary"
          type="button"
          disabled={isGenerating || isImageOperationLocked || !analysis.image_prompt}
          onClick={onGenerate}
        >
          {!analysis.image_prompt
            ? showTechnicalDetails
              ? 'Prompt no disponible'
              : 'No se puede generar'
            : isGenerating
            ? 'Generando composición…'
            : isRetry
              ? 'Reintentar composición'
              : 'Generar composición'}
        </button>
      </div>
      {showTechnicalDetails && analysis.image_prompt && (
        <p className="cost-note">
          Coste estimado de esta composición:{' '}
          <strong>${analysis.flat_lay_estimated_cost.toFixed(3)}</strong>. El análisis de
          texto ya se ha realizado; la llamada de imagen solo empieza al confirmar.
        </p>
      )}
    </section>
  )
}
