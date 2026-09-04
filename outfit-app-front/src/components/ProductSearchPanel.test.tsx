import { fireEvent, render, screen } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import outfitDetailFixture from '../../../contracts/outfit-detail.v1.json'
import type { PersistedOutfit } from '../types/outfit'
import { ProductSearchPanel } from './ProductSearchPanel'

const outfit = outfitDetailFixture as PersistedOutfit

function renderPanel(
  overrides: Partial<Parameters<typeof ProductSearchPanel>[0]> = {},
) {
  const props: Parameters<typeof ProductSearchPanel>[0] = {
    outfitId: outfit.outfit_id,
    items: outfit.outfit.items,
    searchItems: outfit.product_search_items,
    action: null,
    isPaidOperationLocked: false,
    showTechnicalDetails: true,
    onSearch: vi.fn(),
    ...overrides,
  }
  render(<ProductSearchPanel {...props} />)
  return props
}

it('muestra caché, coste y enlaces sin buscar automáticamente', () => {
  const props = renderPanel()

  expect(screen.getByText('Búsqueda guardada')).toBeVisible()
  expect(screen.queryByText('Sin miniatura')).not.toBeInTheDocument()
  expect(screen.getByText('$0.012')).toBeVisible()
  expect(screen.getByText(/pantalón negro comprar online España/)).toBeVisible()
  expect(
    screen.getByRole('button', { name: 'Buscar similares · ≈ $0.03' }),
  ).toBeEnabled()
  expect(screen.getByRole('link', { name: 'Abrir producto' })).toHaveAttribute(
    'target',
    '_blank',
  )
  expect(screen.getByRole('link', { name: 'Abrir producto' })).toHaveAttribute(
    'rel',
    'noopener noreferrer',
  )
  expect(props.onSearch).not.toHaveBeenCalled()
})

it('oculta consultas y costes a una cuenta normal sin bloquear la búsqueda', () => {
  renderPanel({ showTechnicalDetails: false })

  expect(screen.queryByText('$0.012')).not.toBeInTheDocument()
  expect(
    screen.queryByText(/pantalón negro comprar online España/),
  ).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Buscar similares' })).toBeEnabled()
})

it('envía exclusivamente el índice seleccionado tras el clic explícito', () => {
  const props = renderPanel()

  fireEvent.click(
    screen.getByRole('button', { name: 'Buscar similares · ≈ $0.03' }),
  )

  expect(props.onSearch).toHaveBeenCalledOnce()
  expect(props.onSearch).toHaveBeenCalledWith(17, 1, null, false)
})

it('permite afinar una consulta suficiente antes de iniciar la llamada', () => {
  const props = renderPanel()

  fireEvent.change(
    screen.getByRole('textbox', {
      name: 'Añade un detalle para afinar la búsqueda (opcional)',
    }),
    { target: { value: '  tiro alto con pinzas  ' } },
  )
  fireEvent.click(
    screen.getByRole('button', { name: 'Buscar similares · ≈ $0.03' }),
  )

  expect(props.onSearch).toHaveBeenCalledOnce()
  expect(props.onSearch).toHaveBeenCalledWith(
    17,
    1,
    'tiro alto con pinzas',
    false,
  )
})

it('envía marca y atributos desde el mismo campo', () => {
  const props = renderPanel()

  fireEvent.change(
    screen.getByRole('textbox', {
      name: 'Añade un detalle para afinar la búsqueda (opcional)',
    }),
    { target: { value: '  Versace verde militar  ' } },
  )
  fireEvent.click(
    screen.getByRole('button', { name: 'Buscar similares · ≈ $0.03' }),
  )

  expect(props.onSearch).toHaveBeenCalledWith(
    17,
    1,
    'Versace verde militar',
    false,
  )
})

it('mantiene los resultados y bloquea otras acciones durante la búsqueda', () => {
  const props = renderPanel({
    action: {
      status: 'loading',
      outfitId: 17,
      itemIndex: 1,
    },
    isPaidOperationLocked: true,
  })

  expect(screen.getByText('Búsqueda guardada')).toBeVisible()
  expect(screen.getByRole('status')).toHaveTextContent('Buscando productos')
  expect(
    screen.getByRole('button', { name: 'Buscando productos…' }),
  ).toBeDisabled()
  expect(props.onSearch).not.toHaveBeenCalled()
})

it('permite repetir una búsqueda guardada sin perder el resultado anterior', () => {
  const props = renderPanel()

  // La prenda 0 del contrato ya tiene una búsqueda guardada.
  const retry = screen.getByRole('button', { name: 'Buscar de nuevo · ≈ $0.03' })
  expect(retry).toBeEnabled()
  expect(screen.getByText('Búsqueda guardada')).toBeVisible()

  fireEvent.change(
    screen.getByRole('textbox', {
      name: 'Añade o cambia un detalle y vuelve a buscar (opcional)',
    }),
    { target: { value: 'Zara' } },
  )
  fireEvent.click(retry)

  // force_new en true: solo una acción explícita repite una búsqueda pagada.
  expect(props.onSearch).toHaveBeenCalledWith(17, 0, 'Zara', true)
})

it('avisa de la antigüedad de una búsqueda guardada', () => {
  renderPanel()

  expect(screen.getByText(/Buscado el/)).toBeVisible()
  expect(screen.getByText(/pueden haber caducado/)).toBeVisible()
})

it('deja de ofrecer búsqueda cuando la prenda agota sus intentos', () => {
  renderPanel({
    searchItems: [
      {
        ...outfit.product_search_items[0],
        attempts: 3,
        attempts_remaining: 0,
      },
    ],
  })

  expect(
    screen.queryByRole('button', { name: /Buscar/ }),
  ).not.toBeInTheDocument()
  expect(screen.getByText(/agotado las búsquedas/)).toBeVisible()
})

it('exige un detalle escrito cuando la extracción es demasiado genérica', () => {
  const props = renderPanel({
    items: [
      {
        ...outfit.outfit.items[0],
        item_type: 'complemento',
        color: null,
        material: null,
      },
    ],
    searchItems: [
      {
        item_index: 0,
        query: null,
        needs_details: true,
        message: 'Añade un tipo de prenda o accesorio concreto.',
        estimated_cost: 0.03,
        search: null,
        attempts: 0,
        attempts_remaining: 3,
      },
    ],
  })
  const button = screen.getByRole('button', {
    name: 'Buscar similares · ≈ $0.03',
  })

  expect(button).toBeDisabled()
  fireEvent.change(
    screen.getByRole('textbox', {
      name: 'Añade el tipo, marca, color, material, corte o detalle que falta',
    }),
    { target: { value: 'bolso verde pequeño' } },
  )
  expect(button).toBeEnabled()
  fireEvent.click(button)

  expect(props.onSearch).toHaveBeenCalledWith(
    17,
    0,
    'bolso verde pequeño',
    false,
  )
})
