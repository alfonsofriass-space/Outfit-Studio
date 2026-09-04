import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ProposalChoice } from './ProposalChoice'
import type { ProposalSet } from '../types/outfit'

const proposalSet: ProposalSet = {
  status: 'proposals_ready',
  proposal_set_id: 8,
  situation: 'Boda de tarde en octubre, en el campo',
  cost_estimate: 0.00124,
  chosen_indexes: [],
  created_at: '2026-09-03T10:00:00Z',
  models_used: { text_primary: 'gpt-5.4-nano', text_fallback: null, image: null },
  proposals: [
    {
      index: 0,
      title: 'Traje de lino arena',
      outfit_summary: 'Traje ligero de lino arena con camisa blanca.',
      items: [
        {
          category: 'upper',
          item_type: 'americana',
          brand: null,
          color: 'arena',
          material: 'lino',
          fit: null,
          details: [],
          certainty: 'high',
          visual_phrase_en: 'sand linen blazer',
        },
      ],
      styling_notes_en: [],
    },
    {
      index: 1,
      title: 'Chaleco sin americana',
      outfit_summary: 'Camisa celeste con chaleco azul marino.',
      items: [
        {
          category: 'upper',
          item_type: 'chaleco',
          brand: null,
          color: 'azul marino',
          material: null,
          fit: null,
          details: [],
          certainty: 'high',
          visual_phrase_en: 'navy waistcoat',
        },
      ],
      styling_notes_en: [],
    },
    {
      index: 2,
      title: 'Contraste azul noche',
      outfit_summary: 'Americana azul noche con pantalón gris claro.',
      items: [
        {
          category: 'lower',
          item_type: 'pantalón de vestir',
          brand: null,
          color: 'gris claro',
          material: null,
          fit: null,
          details: [],
          certainty: 'high',
          visual_phrase_en: 'light gray trousers',
        },
      ],
      styling_notes_en: [],
    },
  ],
}

function renderChoice(overrides: Partial<Parameters<typeof ProposalChoice>[0]> = {}) {
  const onChoose = vi.fn()
  const onRestart = vi.fn()
  render(
    <ProposalChoice
      choosingIndex={null}
      errorMessage={null}
      proposalSet={proposalSet}
      showTechnicalDetails={false}
      onChoose={onChoose}
      onRestart={onRestart}
      {...overrides}
    />,
  )
  return { onChoose, onRestart }
}

describe('ProposalChoice', () => {
  it('presenta las tres propuestas con la situación que las originó', () => {
    renderChoice()

    expect(screen.getByText('“Boda de tarde en octubre, en el campo”')).toBeVisible()
    expect(screen.getAllByRole('button', { name: 'Elegir esta' })).toHaveLength(3)
    expect(screen.getByRole('heading', { name: 'Traje de lino arena' })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Chaleco sin americana' })).toBeVisible()
    expect(screen.getByRole('heading', { name: 'Contraste azul noche' })).toBeVisible()
  })

  it('devuelve la posición elegida', () => {
    const { onChoose } = renderChoice()

    fireEvent.click(screen.getAllByRole('button', { name: 'Elegir esta' })[1])

    expect(onChoose).toHaveBeenCalledWith(1)
  })

  it('bloquea las tres opciones mientras una se está preparando', () => {
    renderChoice({ choosingIndex: 2 })

    expect(screen.getByRole('button', { name: 'Preparando…' })).toBeDisabled()
    screen
      .getAllByRole('button', { name: 'Elegir esta' })
      .forEach((button) => expect(button).toBeDisabled())
  })

  it('marca la propuesta ya elegida sin cerrar las otras dos', () => {
    renderChoice({ proposalSet: { ...proposalSet, chosen_indexes: [0] } })

    expect(screen.getByText('Ya elegida')).toBeVisible()
    screen
      .getAllByRole('button', { name: 'Elegir esta' })
      .forEach((button) => expect(button).toBeEnabled())
  })

  it('oculta el coste a una cuenta normal y lo muestra al administrador', () => {
    const { unmount } = render(
      <ProposalChoice
        choosingIndex={null}
        errorMessage={null}
        proposalSet={proposalSet}
        showTechnicalDetails={false}
        onChoose={vi.fn()}
        onRestart={vi.fn()}
      />,
    )
    expect(
      screen.getByText('Elegir una propuesta no genera todavía ninguna imagen.'),
    ).toBeVisible()
    unmount()

    renderChoice({ showTechnicalDetails: true })
    expect(screen.getByText(/gpt-5\.4-nano/)).toBeVisible()
  })

  it('ofrece volver a escribir la situación', () => {
    const { onRestart } = renderChoice()

    fireEvent.click(screen.getByRole('button', { name: 'Cambiar la situación' }))

    expect(onRestart).toHaveBeenCalledOnce()
  })
})
