import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import outfitDetailFixture from '../../../contracts/outfit-detail.v1.json'
import type { PersistedOutfit } from '../types/outfit'
import { OutfitWorkspace } from './OutfitWorkspace'

const generated = outfitDetailFixture as PersistedOutfit

afterEach(() => {
  vi.restoreAllMocks()
})

function renderWorkspace(
  outfits: PersistedOutfit[],
  overrides: Partial<Parameters<typeof OutfitWorkspace>[0]> = {},
) {
  const props: Parameters<typeof OutfitWorkspace>[0] = {
    outfits,
    isLoading: false,
    openingOutfitId: null,
    deletingOutfitId: null,
    wornViewAction: null,
    productSearchAction: null,
    isPaidOperationLocked: false,
    showTechnicalDetails: true,
    error: null,
    hasMoreOutfits: false,
    isLoadingMore: false,
    favouritesOnly: false,
    updatingOutfitId: null,
    onLoadMore: vi.fn(),
    onToggleFavouritesOnly: vi.fn(),
    onUpdateOutfit: vi.fn(),
    onContinue: vi.fn(),
    onDelete: vi.fn(),
    onGenerateWornView: vi.fn(),
    onSearchProduct: vi.fn(),
    onRetry: vi.fn(),
    ...overrides,
  }
  render(<OutfitWorkspace {...props} />)
  return props
}

describe('OutfitWorkspace', () => {
  it('agrupa por outfit, cambia de composición y permite descargar cada resultado', () => {
    const props = renderWorkspace([generated])

    expect(screen.getByText('2 composiciones')).toBeVisible()
    // La portada es la composición elegida, no la original.
    expect(screen.getByRole('img', { name: /Portada del outfit/ })).toHaveAttribute(
      'src',
      '/images/variation.png',
    )
    expect(screen.queryByText('Outfit guardado')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: /camisa blanca de lino/i }))

    expect(screen.getByText('Outfit guardado')).toBeVisible()
    const detail = screen.getByRole('region', {
      name: /camisa blanca de lino/i,
    })
    expect(detail).toBeVisible()
    expect(
      screen.getByRole('heading', { name: /camisa blanca de lino/i }),
    ).toHaveFocus()
    expect(screen.getByRole('img', { name: /Variación 1 del outfit/ })).toHaveAttribute(
      'src',
      '/images/variation.png',
    )
    expect(screen.getByRole('img', { name: /Vista puesta del outfit/ })).toHaveAttribute(
      'src',
      '/images/worn.png',
    )
    expect(screen.getByRole('link', { name: 'Descargar composición' })).toHaveAttribute(
      'download',
    )
    expect(screen.getByRole('link', { name: 'Descargar vista puesta' })).toHaveAttribute(
      'download',
    )

    const expandVariation = screen.getByRole('button', {
      name: 'Ampliar variación 1',
    })
    fireEvent.click(expandVariation)

    expect(screen.getByRole('dialog', { name: 'Variación 1' })).toBeVisible()
    expect(
      screen.getByRole('button', { name: 'Cerrar vista ampliada' }),
    ).toHaveFocus()

    fireEvent.click(
      screen.getByRole('button', { name: 'Cerrar vista ampliada' }),
    )

    expect(
      screen.queryByRole('dialog', { name: 'Variación 1' }),
    ).not.toBeInTheDocument()
    expect(expandVariation).toHaveFocus()

    fireEvent.click(
      screen.getByRole('button', { name: 'Mostrar composición original' }),
    )

    expect(
      screen.getByRole('img', { name: /Composición original del outfit/ }),
    ).toHaveAttribute('src', '/images/original.png')
    expect(screen.queryByRole('img', { name: /Vista puesta del outfit/ })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Generar vista puesta' }))
    expect(props.onGenerateWornView).toHaveBeenCalledWith(17, 31)

    fireEvent.click(
      screen.getByRole('button', { name: 'Volver a la biblioteca' }),
    )
    expect(
      screen.getByRole('heading', { name: 'Biblioteca de looks.' }),
    ).toBeVisible()
    expect(
      screen.getByRole('button', { name: /camisa blanca de lino/i }),
    ).toHaveFocus()
    expect(
      screen.queryByRole('region', { name: /camisa blanca de lino/i }),
    ).not.toBeInTheDocument()
  })

  it('continúa un análisis pendiente sin tratarlo como una composición', () => {
    const pending: PersistedOutfit = {
      ...generated,
      outfit_id: 18,
      user_description: 'chaqueta verde y falda negra',
      images: [],
      worn_view_preview: null,
      regeneration_count: 0,
      regenerations_remaining: 3,
    }
    const props = renderWorkspace([pending, generated])

    expect(screen.getByText('Sin composición')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: /chaqueta verde/i }))

    expect(screen.getByText('Análisis guardado')).toBeVisible()
    expect(screen.getByText(/no tiene ninguna composición/i)).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Continuar análisis' }))
    expect(props.onContinue).toHaveBeenCalledWith(18)
  })

  it('muestra el estado de una vista histórica en curso sin duplicar la llamada', () => {
    const props = renderWorkspace([generated], {
      wornViewAction: {
        sourceImageId: 31,
        status: 'loading',
      },
      isPaidOperationLocked: true,
    })

    fireEvent.click(screen.getByRole('button', { name: /camisa blanca de lino/i }))
    fireEvent.click(
      screen.getByRole('button', { name: 'Mostrar composición original' }),
    )

    expect(screen.getByRole('status')).toHaveTextContent('Generando la vista puesta')
    expect(screen.getByRole('button', { name: 'Generando vista puesta…' })).toBeDisabled()
    expect(props.onGenerateWornView).not.toHaveBeenCalled()
  })

  it('muestra una sola explicación tras un fallo de búsqueda y permite reintentar', () => {
    const message =
      'La búsqueda terminó antes de devolver resultados completos. No se ha realizado un reintento automático.'
    renderWorkspace([generated], {
      productSearchAction: {
        outfitId: generated.outfit_id,
        itemIndex: 1,
        status: 'error',
        message,
      },
    })

    fireEvent.click(screen.getByRole('button', { name: /camisa blanca de lino/i }))

    expect(screen.getByRole('alert')).toHaveTextContent(message)
    expect(
      screen.queryByText('No se realizará un reintento automático.'),
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Reintentar búsqueda · ≈ $0.03' }),
    ).toBeEnabled()
  })

  it('abre una única composición directamente sin repetirla en un selector', () => {
    const singleComposition: PersistedOutfit = {
      ...generated,
      images: [generated.images[0]],
      regeneration_count: 0,
      regenerations_remaining: 3,
    }

    renderWorkspace([singleComposition])
    fireEvent.click(screen.getByRole('button', { name: /camisa blanca de lino/i }))

    expect(
      screen.queryByRole('button', { name: 'Mostrar composición original' }),
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole('img', { name: /Composición original del outfit/ }),
    ).toHaveAttribute('src', '/images/original.png')
    expect(screen.getByRole('link', { name: 'Descargar composición' })).toBeVisible()
  })

  it('exige confirmación antes de eliminar el outfit seleccionado', () => {
    const confirmMock = vi
      .spyOn(window, 'confirm')
      .mockReturnValueOnce(false)
      .mockReturnValueOnce(true)
    const props = renderWorkspace([generated])

    fireEvent.click(screen.getByRole('button', { name: /camisa blanca de lino/i }))
    const deleteButton = screen.getByRole('button', { name: 'Eliminar outfit' })
    fireEvent.click(deleteButton)
    expect(confirmMock).toHaveBeenCalledWith(
      '¿Eliminar este outfit y todas sus imágenes guardadas? Esta acción no se puede deshacer.',
    )
    expect(props.onDelete).not.toHaveBeenCalled()

    fireEvent.click(deleteButton)
    expect(props.onDelete).toHaveBeenCalledOnce()
    expect(props.onDelete).toHaveBeenCalledWith(17)
  })
})

describe('portada y archivo', () => {
  it('sin composición elegida usa la última y no la primera', () => {
    renderWorkspace([{ ...generated, chosen_image_id: null }])

    expect(screen.getByRole('img', { name: /Portada del outfit/ })).toHaveAttribute(
      'src',
      '/images/variation.png',
    )
  })

  it('respeta la composición elegida aunque no sea la última', () => {
    renderWorkspace([{ ...generated, chosen_image_id: 31 }])

    expect(screen.getByRole('img', { name: /Portada del outfit/ })).toHaveAttribute(
      'src',
      '/images/original.png',
    )
  })

  it('marca los favoritos en la rejilla', () => {
    renderWorkspace([{ ...generated, is_favourite: true }])

    expect(screen.getByText('Favorito')).toBeVisible()
  })

  it('ofrece cargar más solo cuando quedan outfits por traer', () => {
    const props = renderWorkspace([generated], { hasMoreOutfits: true })

    fireEvent.click(screen.getByRole('button', { name: 'Cargar más' }))

    expect(props.onLoadMore).toHaveBeenCalledOnce()
  })

  it('no ofrece cargar más cuando la última página vino a medias', () => {
    renderWorkspace([generated])

    expect(
      screen.queryByRole('button', { name: 'Cargar más' }),
    ).not.toBeInTheDocument()
  })

  it('cambia el filtro de favoritos', () => {
    const props = renderWorkspace([generated])

    fireEvent.click(screen.getByRole('button', { name: 'Favoritos' }))

    expect(props.onToggleFavouritesOnly).toHaveBeenCalledWith(true)
  })

  it('explica que no hay favoritos en vez de parecer una biblioteca vacía', () => {
    renderWorkspace([], { favouritesOnly: true })

    expect(
      screen.getByText('Todavía no has marcado ningún outfit como favorito.'),
    ).toBeVisible()
  })
})

describe('archivo desde el detalle', () => {
  function openDetail(overrides = {}) {
    const props = renderWorkspace([{ ...generated, ...overrides }])
    fireEvent.click(screen.getByRole('button', { name: /camisa blanca de lino/i }))
    return props
  }

  it('marca y desmarca el favorito desde el detalle', () => {
    const props = openDetail({ is_favourite: false })

    fireEvent.click(screen.getByRole('button', { name: 'Marcar favorito' }))

    expect(props.onUpdateOutfit).toHaveBeenCalledWith(17, { is_favourite: true })
  })

  it('refleja que ya está en favoritos', () => {
    openDetail({ is_favourite: true })

    expect(screen.getByRole('button', { name: 'En favoritos' })).toHaveAttribute(
      'aria-pressed',
      'true',
    )
  })

  it('elige como portada la composición que se está viendo', () => {
    // El detalle abre en la última composición, así que el botón actúa sobre ella.
    const props = openDetail({ chosen_image_id: null })

    fireEvent.click(screen.getByRole('button', { name: 'Usar como portada' }))

    expect(props.onUpdateOutfit).toHaveBeenCalledWith(17, { chosen_image_id: 32 })
  })

  it('no deja volver a elegir la composición que ya es portada', () => {
    openDetail({ chosen_image_id: 32 })

    expect(screen.getByRole('button', { name: 'Es la portada' })).toBeDisabled()
  })

  it('permite cambiar la portada a otra composición del mismo outfit', () => {
    const props = openDetail({ chosen_image_id: 32 })

    fireEvent.click(
      screen.getByRole('button', { name: 'Mostrar composición original' }),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Usar como portada' }))

    expect(props.onUpdateOutfit).toHaveBeenCalledWith(17, { chosen_image_id: 31 })
  })

  it('explica en el detalle qué accesorios quedaron fuera del board', () => {
    openDetail({ accessories_omitted: ['gorra roja', 'reloj'] })

    expect(
      screen.getByText(
        'Fuera de la composición por el límite del board: gorra roja, reloj.',
      ),
    ).toBeVisible()
  })
})
