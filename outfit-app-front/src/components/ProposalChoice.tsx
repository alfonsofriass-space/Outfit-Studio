import type { OutfitItem, ProposalSet } from '../types/outfit'

function garmentLine(item: OutfitItem): string {
  const attributes = [item.color, item.material, item.fit].filter(
    (value): value is string => Boolean(value),
  )
  return attributes.length > 0
    ? `${item.item_type} · ${attributes.join(', ')}`
    : item.item_type
}

interface ProposalChoiceProps {
  proposalSet: ProposalSet
  choosingIndex: number | null
  errorMessage: string | null
  showTechnicalDetails: boolean
  onChoose: (proposalIndex: number) => void
  onRestart: () => void
}

export function ProposalChoice({
  proposalSet,
  choosingIndex,
  errorMessage,
  showTechnicalDetails,
  onChoose,
  onRestart,
}: ProposalChoiceProps) {
  const isChoosing = choosingIndex !== null

  return (
    <section className="proposals" aria-labelledby="proposals-title">
      <div className="review__description">
        <p className="eyebrow">Tu situación</p>
        <p>“{proposalSet.situation}”</p>
      </div>

      <div className="section-heading">
        <span className="section-heading__index" aria-hidden="true">
          01
        </span>
        <div>
          <h2 id="proposals-title">Tres propuestas</h2>
          <span>Elige una para revisarla antes de generar</span>
        </div>
      </div>

      {errorMessage && (
        <div className="notice notice--compact" role="alert">
          <strong>{errorMessage}</strong>
          <span>
            No se ha creado ningún outfit. Puedes volver a elegir una propuesta.
          </span>
        </div>
      )}

      <ol className="proposal-list">
        {proposalSet.proposals.map((proposal) => {
          const isChosen = proposalSet.chosen_indexes.includes(proposal.index)

          return (
            <li className="proposal" key={proposal.index}>
              <span className="proposal__index" aria-hidden="true">
                {`0${proposal.index + 1}`}
              </span>
              <div className="proposal__body">
                <div className="proposal__head">
                  <h3>{proposal.title}</h3>
                  {isChosen && <span className="proposal__mark">Ya elegida</span>}
                </div>
                <p className="proposal__summary">{proposal.outfit_summary}</p>
                <ul
                  className="proposal__garments"
                  aria-label={`Prendas de ${proposal.title}`}
                >
                  {proposal.items.map((item, index) => (
                    <li key={`${item.category}-${index}`}>{garmentLine(item)}</li>
                  ))}
                </ul>
                <button
                  className="button button--primary"
                  type="button"
                  disabled={isChoosing}
                  onClick={() => onChoose(proposal.index)}
                >
                  {choosingIndex === proposal.index ? (
                    'Preparando…'
                  ) : (
                    <>
                      Elegir esta <span aria-hidden="true">→</span>
                    </>
                  )}
                </button>
              </div>
            </li>
          )
        })}
      </ol>

      <div className="proposal-actions">
        <button
          className="button button--secondary"
          type="button"
          disabled={isChoosing}
          onClick={onRestart}
        >
          Cambiar la situación
        </button>
        <p className="cost-note">
          {showTechnicalDetails
            ? `Elegir no cuesta nada: la propuesta ya está guardada. Estas tres costaron $${proposalSet.cost_estimate.toFixed(6)} con ${proposalSet.models_used.text_primary}.`
            : 'Elegir una propuesta no genera todavía ninguna imagen.'}
        </p>
      </div>
    </section>
  )
}
