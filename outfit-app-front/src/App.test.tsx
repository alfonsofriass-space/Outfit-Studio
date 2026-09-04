import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import outfitDetailFixture from '../../contracts/outfit-detail.v1.json'
import App, { OutfitApp } from './App'
import type { AuthenticatedUser } from './api/auth'
import type { PersistedOutfit } from './types/outfit'

const persistedOutfit = outfitDetailFixture as PersistedOutfit

const adminUser: AuthenticatedUser = {
  id: 1,
  username: 'admin',
  role: 'admin',
  is_active: true,
  created_at: '2026-08-09T10:00:00Z',
}

const normalUser: AuthenticatedUser = {
  ...adminUser,
  id: 2,
  username: 'persona',
  role: 'user',
}

function renderOutfitApp(currentUser: AuthenticatedUser = adminUser) {
  return render(
    <OutfitApp
      currentUser={currentUser}
      onLogout={async () => undefined}
    />,
  )
}

const analysis = {
  status: 'completed',
  outfit_id: 17,
  user_description: 'camisa blanca de lino metida dentro del pantalón negro',
  outfit: {
    status: 'ok',
    outfit_summary: 'Look sobrio con la camisa metida dentro del pantalón',
    items: [
      {
        category: 'upper',
        item_type: 'camisa',
        color: 'blanca',
        material: 'lino',
        fit: null,
        details: [],
        certainty: 'high',
        visual_phrase_en: 'white linen shirt',
      },
      {
        category: 'lower',
        item_type: 'pantalón',
        color: 'negro',
        material: null,
        fit: null,
        details: [],
        certainty: 'high',
        visual_phrase_en: 'black trousers',
      },
    ],
    styling_notes_en: ['white linen shirt tucked into black trousers'],
  },
  image: null,
  image_id: null,
  image_error: null,
  image_prompt:
    'Top area: white linen shirt. Middle area: black trousers. Explicit styling: white linen shirt tucked into black trousers.',
  flat_lay_estimated_cost: 0.006,
  accessories_omitted: [],
  worn_view_preview: null,
  product_search_items: persistedOutfit.product_search_items,
  models_used: {
    text_primary: 'text-model',
    text_fallback: null,
    image: null,
  },
}

const generation = {
  status: 'regenerated',
  outfit_id: 17,
  image_id: 31,
  image: {
    model: 'image-model',
    quality: 'low',
    size: '1024x1024',
    url_or_base64: '/images/outfit.png',
  },
  generation_prompt: analysis.image_prompt,
  regeneration_count: 0,
  regenerations_remaining: 3,
  worn_view_preview: {
    generation_prompt:
      'Use the supplied flat-lay board as the visual source of truth. Dress one neutral mannequin.',
    estimated_cost: 0.015,
  },
}

const wornViewResponse = {
  status: 'worn_view_ready',
  created: true,
  outfit_id: 17,
  source_image_id: 31,
  worn_view: {
    worn_view_id: 5,
    source_image_id: 31,
    generation_prompt: generation.worn_view_preview.generation_prompt,
    image: {
      model: 'gpt-image-2',
      quality: 'low',
      size: '1024x1536',
      url_or_base64: '/images/worn.png',
    },
    cost_estimate: 0.014252,
    created_at: '2026-07-20T19:03:12Z',
  },
}

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

function isBackgroundList(url: string): boolean {
  return url.startsWith('/outfits?')
}

afterEach(() => {
  window.localStorage.clear()
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('sesión de usuario', () => {
  it('muestra el acceso y entra con una cuenta existente', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = input.toString()
      if (url === '/auth/me') {
        return Promise.resolve(jsonResponse({ detail: 'Debes iniciar sesión.' }, 401))
      }
      if (url === '/auth/login') return Promise.resolve(jsonResponse(adminUser))
      if (isBackgroundList(url)) return Promise.resolve(jsonResponse([]))
      return Promise.resolve(jsonResponse({ detail: 'Ruta inesperada' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)

    expect(await screen.findByRole('heading', { name: 'Entra en tu biblioteca.' })).toBeVisible()
    fireEvent.change(screen.getByRole('textbox', { name: /Nombre de usuario/ }), {
      target: { value: 'admin' },
    })
    fireEvent.change(screen.getByLabelText(/Contraseña/), {
      target: { value: 'test' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Entrar en el estudio' }))

    expect(
      await screen.findByRole('heading', { name: 'Describe lo que tienes en mente.' }),
    ).toBeVisible()
    expect(fetchMock).toHaveBeenCalledWith('/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: 'admin', password: 'test' }),
    })
  })

  it('permite crear una cuenta normal desde la misma pantalla', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = input.toString()
      if (url === '/auth/me') {
        return Promise.resolve(jsonResponse({ detail: 'Debes iniciar sesión.' }, 401))
      }
      if (url === '/auth/register') return Promise.resolve(jsonResponse(normalUser, 201))
      if (isBackgroundList(url)) return Promise.resolve(jsonResponse([]))
      return Promise.resolve(jsonResponse({ detail: 'Ruta inesperada' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)

    fireEvent.click(await screen.findByRole('tab', { name: 'Crear cuenta' }))
    fireEvent.change(screen.getByRole('textbox', { name: /Nombre de usuario/ }), {
      target: { value: 'persona' },
    })
    fireEvent.change(screen.getByLabelText(/Contraseña/), {
      target: { value: 'clave' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Crear mi cuenta' }))

    expect(await screen.findByText('persona')).toBeVisible()
    expect(fetchMock).toHaveBeenCalledWith('/auth/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: 'persona', password: 'clave' }),
    })
  })

  it('cierra la sesión y limpia el outfit activo', async () => {
    window.localStorage.setItem('active_outfit_id', '17')
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = input.toString()
      if (url === '/auth/me') return Promise.resolve(jsonResponse(adminUser))
      if (url === '/auth/logout') {
        return Promise.resolve(new Response(null, { status: 204 }))
      }
      if (url === '/outfits/17') {
        return Promise.resolve(jsonResponse({ detail: 'Outfit no encontrado' }, 404))
      }
      if (isBackgroundList(url)) return Promise.resolve(jsonResponse([]))
      return Promise.resolve(jsonResponse({ detail: 'Ruta inesperada' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)

    render(<App />)

    await screen.findByRole('textbox', { name: 'Describe tu outfit' })
    fireEvent.click(screen.getByRole('button', { name: 'Cerrar sesión' }))

    expect(await screen.findByRole('heading', { name: 'Entra en tu biblioteca.' })).toBeVisible()
    expect(window.localStorage.getItem('active_outfit_id')).toBeNull()
    expect(fetchMock).toHaveBeenCalledWith('/auth/logout', { method: 'POST' })
  })
})

describe('flujo principal', () => {
  it('analiza, enseña el prompt y espera confirmación antes de generar imagen', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, _options?: RequestInit) => {
      const url = input.toString()
      if (isBackgroundList(url)) return Promise.resolve(jsonResponse([]))
      if (url === '/outfits/generate') return Promise.resolve(jsonResponse(analysis))
      if (url === '/outfits/17/regenerate') return Promise.resolve(jsonResponse(generation))
      return Promise.resolve(jsonResponse({ detail: 'Ruta inesperada' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)

    renderOutfitApp()

    fireEvent.change(screen.getByRole('textbox', { name: 'Describe tu outfit' }), {
      target: { value: analysis.user_description },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Revisar outfit' }))

    expect(await screen.findByText('Paso 2 de 3')).toBeVisible()
    expect(screen.getByText('$0.006')).toBeVisible()
    fireEvent.click(screen.getByText('Ver prompt técnico exacto'))
    expect(screen.getByText('Esto es lo que verá el agente de imagen')).toBeVisible()
    expect(screen.getByText(analysis.image_prompt)).toBeVisible()
    expect(screen.getByText('Relaciones explícitas interpretadas')).toBeVisible()
    expect(screen.getByText('white linen shirt tucked into black trousers')).toBeVisible()
    expect(fetchMock).not.toHaveBeenCalledWith(
      '/outfits/17/regenerate',
      expect.anything(),
    )

    fireEvent.click(screen.getByRole('button', { name: 'Generar composición' }))

    expect(await screen.findByRole('img', { name: /Outfit generado a partir/ })).toHaveAttribute(
      'src',
      '/images/outfit.png',
    )
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/outfits/17/regenerate', { method: 'POST' })
    })
    expect(screen.getByText(generation.worn_view_preview.generation_prompt)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Generar vista puesta/ })).toBeVisible()
    expect(
      fetchMock.mock.calls.some(([input]) => input.toString().endsWith('/worn-view')),
    ).toBe(false)
  })

  it('ofrece buscar prendas en el propio resultado, sin ir a la biblioteca', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = input.toString()
      if (isBackgroundList(url)) return Promise.resolve(jsonResponse([]))
      if (url === '/outfits/generate') return Promise.resolve(jsonResponse(analysis))
      if (url === '/outfits/17/regenerate') return Promise.resolve(jsonResponse(generation))
      return Promise.resolve(jsonResponse({ detail: 'Ruta inesperada' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)

    renderOutfitApp()
    fireEvent.change(screen.getByRole('textbox', { name: 'Describe tu outfit' }), {
      target: { value: analysis.user_description },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Revisar outfit' }))
    expect(await screen.findByText('Paso 2 de 3')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Generar composición' }))
    await screen.findByRole('img', { name: /Outfit generado a partir/ })

    expect(screen.getByText('Prendas encontradas')).toBeVisible()
    // Abrir el resultado nunca dispara una búsqueda por su cuenta.
    expect(
      fetchMock.mock.calls.some(([input]) =>
        input.toString().includes('product-search'),
      ),
    ).toBe(false)
  })

  it('reutiliza el análisis al editar la descripción en vez de dejarlo huérfano', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, _options?: RequestInit) => {
      const url = input.toString()
      if (isBackgroundList(url)) return Promise.resolve(jsonResponse([]))
      if (url === '/outfits/generate') return Promise.resolve(jsonResponse(analysis))
      return Promise.resolve(jsonResponse({ detail: 'Ruta inesperada' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)

    renderOutfitApp()
    fireEvent.change(screen.getByRole('textbox', { name: 'Describe tu outfit' }), {
      target: { value: analysis.user_description },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Revisar outfit' }))
    expect(await screen.findByText('Paso 2 de 3')).toBeVisible()

    fireEvent.click(screen.getByRole('button', { name: 'Editar descripción' }))
    fireEvent.change(screen.getByRole('textbox', { name: 'Describe tu outfit' }), {
      target: { value: 'camisa blanca de lino y pantalón negro de vestir' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Revisar outfit' }))
    expect(await screen.findByText('Paso 2 de 3')).toBeVisible()

    const analyses = fetchMock.mock.calls.filter(
      ([input]) => input.toString() === '/outfits/generate',
    )
    expect(analyses).toHaveLength(2)
    // El primer análisis crea; el segundo reescribe esa misma fila.
    expect(JSON.parse(String(analyses[0][1]?.body)).replace_outfit_id).toBeNull()
    expect(JSON.parse(String(analyses[1][1]?.body)).replace_outfit_id).toBe(17)
  })

  it('mantiene el flujo de creación y oculta la información técnica a una cuenta normal', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = input.toString()
      if (isBackgroundList(url)) return Promise.resolve(jsonResponse([]))
      if (url === '/outfits/generate') return Promise.resolve(jsonResponse(analysis))
      if (url === '/outfits/17/regenerate') {
        return Promise.resolve(jsonResponse(generation))
      }
      return Promise.resolve(jsonResponse({ detail: 'Ruta inesperada' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)

    renderOutfitApp(normalUser)

    expect(screen.getByText('Cuenta personal')).toBeVisible()
    expect(
      screen.getByText(/comprobar las prendas interpretadas/i),
    ).toBeVisible()
    expect(screen.queryByText('Control de gasto:')).not.toBeInTheDocument()

    fireEvent.change(screen.getByRole('textbox', { name: 'Describe tu outfit' }), {
      target: { value: analysis.user_description },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Revisar outfit' }))

    expect(await screen.findByText('Prendas interpretadas')).toBeVisible()
    expect(screen.queryByText('Ver prompt técnico exacto')).not.toBeInTheDocument()
    expect(
      screen.queryByText('Relaciones explícitas interpretadas'),
    ).not.toBeInTheDocument()
    expect(screen.queryByText('$0.006')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Generar composición' }))

    expect(
      await screen.findByRole('img', { name: /Outfit generado a partir/ }),
    ).toBeVisible()
    expect(screen.queryByText('Modelo')).not.toBeInTheDocument()
    expect(screen.queryByText('Formato')).not.toBeInTheDocument()
    expect(
      screen.queryByText(generation.worn_view_preview.generation_prompt),
    ).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Generar vista puesta' }),
    ).toBeVisible()
    expect(screen.queryByText('$0.015')).not.toBeInTheDocument()
  })

  it('mantiene el flat-lay visible y muestra la vista puesta tras una única confirmación', async () => {
    let resolveWornView: (response: Response) => void = () => undefined
    const pendingWornView = new Promise<Response>((resolve) => {
      resolveWornView = resolve
    })
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = input.toString()
      if (isBackgroundList(url)) return Promise.resolve(jsonResponse([]))
      if (url === '/outfits/generate') return Promise.resolve(jsonResponse(analysis))
      if (url === '/outfits/17/regenerate') return Promise.resolve(jsonResponse(generation))
      if (url === '/outfits/17/images/31/worn-view') return pendingWornView
      return Promise.resolve(jsonResponse({ detail: 'Ruta inesperada' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)

    renderOutfitApp()
    fireEvent.change(screen.getByRole('textbox', { name: 'Describe tu outfit' }), {
      target: { value: analysis.user_description },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Revisar outfit' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Generar composición' }))

    const flatLay = await screen.findByRole('img', { name: /Outfit generado a partir/ })
    fireEvent.click(screen.getByRole('button', { name: /Generar vista puesta/ }))

    expect(flatLay).toBeVisible()
    expect(screen.getByRole('button', { name: 'Generando vista puesta…' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Generar otra composición' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Nuevo outfit' })).toBeDisabled()

    fireEvent.click(screen.getByRole('button', { name: 'Biblioteca' }))
    expect(
      screen.getByRole('heading', { name: 'Biblioteca de looks.' }),
    ).toHaveFocus()
    expect(screen.getByRole('status')).toHaveTextContent('Generando vista puesta…')
    fireEvent.click(screen.getByRole('button', { name: 'Crear' }))
    expect(
      screen.getByRole('img', { name: /Outfit generado a partir/ }),
    ).toBeVisible()

    resolveWornView(jsonResponse(wornViewResponse))

    expect(await screen.findByRole('img', { name: /Vista puesta del outfit/ })).toHaveAttribute(
      'src',
      '/images/worn.png',
    )
    expect(fetchMock).toHaveBeenCalledWith('/outfits/17/images/31/worn-view', {
      method: 'POST',
    })
  })

  it('mantiene la composición y permite reintento manual si falla la vista puesta', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = input.toString()
      if (isBackgroundList(url)) return Promise.resolve(jsonResponse([]))
      if (url === '/outfits/generate') return Promise.resolve(jsonResponse(analysis))
      if (url === '/outfits/17/regenerate') return Promise.resolve(jsonResponse(generation))
      if (url === '/outfits/17/images/31/worn-view') {
        return Promise.resolve(
          jsonResponse({ detail: 'No se pudo generar la vista puesta.' }, 502),
        )
      }
      return Promise.resolve(jsonResponse({ detail: 'Ruta inesperada' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)

    renderOutfitApp()
    fireEvent.change(screen.getByRole('textbox', { name: 'Describe tu outfit' }), {
      target: { value: analysis.user_description },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Revisar outfit' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Generar composición' }))
    const flatLay = await screen.findByRole('img', { name: /Outfit generado a partir/ })
    fireEvent.click(screen.getByRole('button', { name: /Generar vista puesta/ }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'No se pudo generar la vista puesta.',
    )
    expect(flatLay).toBeVisible()
    expect(screen.getByRole('button', { name: /Reintentar vista puesta/ })).toBeEnabled()
    expect(
      fetchMock.mock.calls.filter(
        ([input]) => input.toString() === '/outfits/17/images/31/worn-view',
      ),
    ).toHaveLength(1)
  })

  it('mantiene la imagen anterior y permite reintentar una variación fallida', async () => {
    const variation = {
      ...generation,
      image_id: 32,
      image: {
        ...generation.image,
        url_or_base64: '/images/variation.png',
      },
      regeneration_count: 1,
      regenerations_remaining: 2,
    }
    let resolveVariation: (response: Response) => void = () => undefined
    const pendingVariation = new Promise<Response>((resolve) => {
      resolveVariation = resolve
    })
    let generationCalls = 0
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = input.toString()
      if (isBackgroundList(url)) return Promise.resolve(jsonResponse([]))
      if (url === '/outfits/generate') return Promise.resolve(jsonResponse(analysis))
      if (url === '/outfits/17/regenerate') {
        generationCalls += 1
        if (generationCalls === 1) return Promise.resolve(jsonResponse(generation))
        if (generationCalls === 2) return pendingVariation
        return Promise.resolve(jsonResponse(variation))
      }
      return Promise.resolve(jsonResponse({ detail: 'Ruta inesperada' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)

    renderOutfitApp()
    fireEvent.change(screen.getByRole('textbox', { name: 'Describe tu outfit' }), {
      target: { value: analysis.user_description },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Revisar outfit' }))
    fireEvent.click(await screen.findByRole('button', { name: 'Generar composición' }))

    const originalImage = await screen.findByRole('img', { name: /Outfit generado a partir/ })
    expect(originalImage).toHaveAttribute('src', '/images/outfit.png')
    fireEvent.click(screen.getByRole('button', { name: 'Generar otra composición' }))

    expect(originalImage).toBeVisible()
    expect(screen.getByRole('button', { name: 'Generando composición…' })).toBeDisabled()

    resolveVariation(
      jsonResponse(
        { detail: 'No se pudo generar la imagen. Inténtalo de nuevo.' },
        502,
      ),
    )
    const retryButton = await screen.findByRole('button', {
      name: 'Reintentar variación',
    })
    expect(originalImage).toBeVisible()
    fireEvent.click(retryButton)

    await waitFor(() => {
      expect(screen.getByRole('img', { name: /Outfit generado a partir/ })).toHaveAttribute(
        'src',
        '/images/variation.png',
      )
    })
  })

  it('muestra la aclaración y no ofrece generar cuando el texto es insuficiente', async () => {
    const clarification = {
      status: 'needs_clarification',
      message: 'Necesito al menos una prenda con algún detalle visual.',
      suggestion: 'Por ejemplo: abrigo largo de lana.',
    }
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = input.toString()
      return Promise.resolve(jsonResponse(isBackgroundList(url) ? [] : clarification))
    })
    vi.stubGlobal('fetch', fetchMock)

    renderOutfitApp()
    fireEvent.change(screen.getByRole('textbox', { name: 'Describe tu outfit' }), {
      target: { value: 'abrigo' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Revisar outfit' }))

    expect(await screen.findByText(clarification.message)).toBeVisible()
    expect(
      screen.queryByRole('button', { name: 'Generar composición' }),
    ).not.toBeInTheDocument()
  })

  it('restaura el último resultado y su vista puesta usando solo active_outfit_id', async () => {
    window.localStorage.setItem('active_outfit_id', String(persistedOutfit.outfit_id))
    const fetchMock = vi.fn((input: RequestInfo | URL, _options?: RequestInit) => {
      const url = input.toString()
      if (url === `/outfits/${persistedOutfit.outfit_id}`) {
        return Promise.resolve(jsonResponse(persistedOutfit))
      }
      if (url.startsWith('/outfits?')) return Promise.resolve(jsonResponse([persistedOutfit]))
      return Promise.resolve(jsonResponse({ detail: 'Ruta inesperada' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)

    renderOutfitApp()

    expect(await screen.findByRole('img', { name: /Outfit generado a partir/ })).toHaveAttribute(
      'src',
      '/images/variation.png',
    )
    expect(screen.getByRole('img', { name: /Vista puesta del outfit/ })).toHaveAttribute(
      'src',
      '/images/worn.png',
    )
    expect(screen.getByText('2 variaciones disponibles')).toBeVisible()
    expect(window.localStorage).toHaveLength(1)
    expect(window.localStorage.getItem('active_outfit_id')).toBe('17')
    expect(fetchMock.mock.calls.every(([, options]) => options?.method !== 'POST')).toBe(true)
  })

  it('navega sin perder el outfit y solo lo limpia con Nuevo outfit', async () => {
    window.localStorage.setItem('active_outfit_id', String(persistedOutfit.outfit_id))
    const fetchMock = vi.fn((input: RequestInfo | URL, _options?: RequestInit) => {
      const url = input.toString()
      if (url === `/outfits/${persistedOutfit.outfit_id}`) {
        return Promise.resolve(jsonResponse(persistedOutfit))
      }
      if (isBackgroundList(url)) return Promise.resolve(jsonResponse([persistedOutfit]))
      return Promise.resolve(jsonResponse({ detail: 'Ruta inesperada' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)

    renderOutfitApp()

    expect(await screen.findByRole('img', { name: /Outfit generado a partir/ })).toBeVisible()
    expect(
      screen.queryByRole('heading', { name: 'Biblioteca de looks.' }),
    ).not.toBeInTheDocument()
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2))
    const callsBeforeNavigation = fetchMock.mock.calls.length

    fireEvent.click(screen.getByRole('button', { name: 'Biblioteca' }))
    expect(
      screen.getByRole('heading', { name: 'Biblioteca de looks.' }),
    ).toBeVisible()
    expect(
      screen.queryByRole('img', { name: /Outfit generado a partir/ }),
    ).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Ir a crear outfit' }))
    expect(screen.getByRole('img', { name: /Outfit generado a partir/ })).toBeVisible()
    expect(
      screen.getByRole('heading', {
        name: 'Tu composición está lista.',
      }),
    ).toHaveFocus()
    expect(window.localStorage.getItem('active_outfit_id')).toBe('17')
    expect(fetchMock).toHaveBeenCalledTimes(callsBeforeNavigation)

    fireEvent.click(screen.getByRole('button', { name: 'Nuevo outfit' }))
    expect(screen.getByRole('textbox', { name: 'Describe tu outfit' })).toHaveValue('')
    expect(screen.queryByRole('img', { name: /Outfit generado a partir/ })).not.toBeInTheDocument()
    expect(window.localStorage.getItem('active_outfit_id')).toBeNull()
    expect(fetchMock).toHaveBeenCalledTimes(callsBeforeNavigation)
    expect(fetchMock.mock.calls.every(([, options]) => options?.method !== 'POST')).toBe(true)
  })

  it('restaura un análisis pendiente y permite generar sin repetir el modelo de texto', async () => {
    const pendingOutfit: PersistedOutfit = {
      ...persistedOutfit,
      outfit_id: 18,
      images: [],
      worn_view_preview: null,
      regeneration_count: 0,
      regenerations_remaining: 3,
    }
    window.localStorage.setItem('active_outfit_id', '18')
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = input.toString()
      if (url === '/outfits/18') return Promise.resolve(jsonResponse(pendingOutfit))
      if (url.startsWith('/outfits?')) return Promise.resolve(jsonResponse([pendingOutfit]))
      if (url === '/outfits/18/regenerate') return Promise.resolve(jsonResponse(generation))
      return Promise.resolve(jsonResponse({ detail: 'Ruta inesperada' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)

    renderOutfitApp()

    expect(await screen.findByText('Paso 2 de 3')).toBeVisible()
    expect(screen.getByText(pendingOutfit.image_prompt as string)).toBeInTheDocument()
    expect(
      fetchMock.mock.calls.some(([input]) => input.toString() === '/outfits/generate'),
    ).toBe(false)
  })

  it('genera una vista puesta desde una composición histórica y actualiza el archivo', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = input.toString()
      if (url.startsWith('/outfits?')) {
        return Promise.resolve(jsonResponse([persistedOutfit]))
      }
      if (url === '/outfits/17/images/31/worn-view') {
        return Promise.resolve(jsonResponse(wornViewResponse))
      }
      if (url === '/outfits/17') {
        return Promise.resolve(jsonResponse(persistedOutfit))
      }
      return Promise.resolve(jsonResponse({ detail: 'Ruta inesperada' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)

    renderOutfitApp()

    fireEvent.click(screen.getByRole('button', { name: 'Biblioteca' }))
    fireEvent.click(
      await screen.findByRole('button', { name: /camisa blanca de lino/i }),
    )
    fireEvent.click(
      screen.getByRole('button', { name: 'Mostrar composición original' }),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Generar vista puesta' }))

    expect(await screen.findByRole('img', { name: /Vista puesta del outfit/ })).toHaveAttribute(
      'src',
      '/images/worn.png',
    )
    expect(fetchMock).toHaveBeenCalledWith('/outfits/17/images/31/worn-view', {
      method: 'POST',
    })
    expect(
      fetchMock.mock.calls.some(([input]) => input.toString().includes('/outfits/gallery')),
    ).toBe(false)

    fireEvent.click(
      screen.getByRole('button', { name: 'Continuar en el generador' }),
    )
    expect(
      await screen.findByRole('img', { name: /Outfit generado a partir/ }),
    ).toHaveAttribute('src', '/images/variation.png')
    expect(screen.getByRole('button', { name: 'Crear' })).toHaveAttribute(
      'aria-current',
      'page',
    )
  })

  it('busca una prenda solo tras el clic y conserva el resultado en el archivo', async () => {
    let resolveSearch: (response: Response) => void = () => undefined
    const pendingSearch = new Promise<Response>((resolve) => {
      resolveSearch = resolve
    })
    const searchResponse = {
      status: 'product_search_ready',
      created: true,
      outfit_id: 17,
      item_index: 1,
      search: {
        item_index: 1,
        query: 'pantalón negro comprar online España',
        additional_details: null,
        candidates: [
          {
            title: 'Pantalón recto negro',
            store: 'Zara',
            product_url: 'https://www.zara.com/es/es/pantalon-negro-p0123.html',
            price_text: '35,95 €',
          },
        ],
        model: 'gpt-5.4-nano',
        web_search_calls: 1,
        input_tokens: 8200,
        output_tokens: 390,
        cost_estimate: 0.012128,
        created_at: '2026-07-27T20:00:00Z',
      },
    }
    const fetchMock = vi.fn(
      (input: RequestInfo | URL, _options?: RequestInit) => {
        const url = input.toString()
        if (isBackgroundList(url)) {
          return Promise.resolve(jsonResponse([persistedOutfit]))
        }
        if (url === '/outfits/17/items/1/product-search') {
          return pendingSearch
        }
        return Promise.resolve(jsonResponse({ detail: 'Ruta inesperada' }, 404))
      },
    )
    vi.stubGlobal('fetch', fetchMock)

    renderOutfitApp()

    fireEvent.click(screen.getByRole('button', { name: 'Biblioteca' }))
    fireEvent.click(
      await screen.findByRole('button', { name: /camisa blanca de lino/i }),
    )
    expect(
      fetchMock.mock.calls.some(
        ([input]) => input.toString() === '/outfits/17/items/1/product-search',
      ),
    ).toBe(false)
    fireEvent.click(
      screen.getByRole('button', { name: 'Buscar similares · ≈ $0.03' }),
    )

    expect(
      screen.getByRole('button', { name: 'Buscando productos…' }),
    ).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Nuevo outfit' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Eliminar outfit' })).toBeDisabled()
    resolveSearch(jsonResponse(searchResponse))

    expect(await screen.findByText('Pantalón recto negro')).toBeVisible()
    expect(fetchMock).toHaveBeenCalledWith(
      '/outfits/17/items/1/product-search',
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ additional_details: null, force_new: false }),
      },
    )
  })

  it('elimina el outfit activo, limpia su estado local y lo retira del archivo', async () => {
    window.localStorage.setItem('active_outfit_id', String(persistedOutfit.outfit_id))
    const fetchMock = vi.fn(
      (input: RequestInfo | URL, options?: RequestInit) => {
        const url = input.toString()
        if (
          url === `/outfits/${persistedOutfit.outfit_id}` &&
          options?.method === 'DELETE'
        ) {
          return Promise.resolve(new Response(null, { status: 204 }))
        }
        if (url === `/outfits/${persistedOutfit.outfit_id}`) {
          return Promise.resolve(jsonResponse(persistedOutfit))
        }
        if (isBackgroundList(url)) {
          return Promise.resolve(jsonResponse([persistedOutfit]))
        }
        return Promise.resolve(jsonResponse({ detail: 'Ruta inesperada' }, 404))
      },
    )
    vi.stubGlobal('fetch', fetchMock)
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    renderOutfitApp()

    expect(await screen.findByRole('img', { name: /Outfit generado a partir/ })).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Biblioteca' }))
    fireEvent.click(
      await screen.findByRole('button', { name: /camisa blanca de lino/i }),
    )
    fireEvent.click(screen.getByRole('button', { name: 'Eliminar outfit' }))

    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledWith('/outfits/17', { method: 'DELETE' })
    })
    expect(
      await screen.findByRole('textbox', { name: 'Describe tu outfit' }),
    ).toHaveValue('')
    expect(window.localStorage.getItem('active_outfit_id')).toBeNull()
    fireEvent.click(screen.getByRole('button', { name: 'Biblioteca' }))
    expect(
      screen.getByText('Los análisis y composiciones que guardes aparecerán aquí.'),
    ).toBeVisible()
    expect(
      screen.queryByRole('button', { name: /camisa blanca de lino/i }),
    ).not.toBeInTheDocument()
  })

  it('descarta un active_outfit_id que ya no existe', async () => {
    window.localStorage.setItem('active_outfit_id', '9999')
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = input.toString()
      if (url === '/outfits/9999') {
        return Promise.resolve(jsonResponse({ detail: 'Outfit no encontrado' }, 404))
      }
      if (isBackgroundList(url)) return Promise.resolve(jsonResponse([]))
      return Promise.resolve(jsonResponse({ detail: 'Ruta inesperada' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)

    renderOutfitApp()

    expect(await screen.findByRole('textbox', { name: 'Describe tu outfit' })).toBeVisible()
    expect(window.localStorage.getItem('active_outfit_id')).toBeNull()
  })
})

const proposalSet = {
  status: 'proposals_ready',
  proposal_set_id: 8,
  situation: 'boda de tarde en octubre en el campo',
  cost_estimate: 0.00124,
  chosen_indexes: [],
  created_at: '2026-09-03T10:00:00Z',
  models_used: { text_primary: 'gpt-5.4-nano', text_fallback: null, image: null },
  proposals: [
    {
      index: 0,
      title: 'Traje de lino arena',
      outfit_summary: 'Traje ligero de lino arena con camisa blanca.',
      items: analysis.outfit.items,
      styling_notes_en: [],
    },
    {
      index: 1,
      title: 'Chaleco sin americana',
      outfit_summary: 'Camisa celeste con chaleco azul marino.',
      items: analysis.outfit.items,
      styling_notes_en: [],
    },
    {
      index: 2,
      title: 'Contraste azul noche',
      outfit_summary: 'Americana azul noche con pantalón gris claro.',
      items: analysis.outfit.items,
      styling_notes_en: [],
    },
  ],
}

function switchToInspiration() {
  fireEvent.click(screen.getByRole('radio', { name: 'Inspírame' }))
}

describe('vía de inspiración', () => {
  it('cambia lo que pide el compositor sin tocar la vía de descripción', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = input.toString()
      if (isBackgroundList(url)) return Promise.resolve(jsonResponse([]))
      return Promise.resolve(jsonResponse({ detail: 'Ruta inesperada' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)
    renderOutfitApp()

    expect(
      await screen.findByRole('textbox', { name: 'Describe tu outfit' }),
    ).toBeVisible()
    expect(screen.getByRole('button', { name: 'Revisar outfit' })).toBeVisible()

    switchToInspiration()

    expect(
      screen.getByRole('textbox', { name: 'Cuéntame la situación' }),
    ).toBeVisible()
    expect(
      screen.getByRole('button', { name: 'Proponer tres outfits' }),
    ).toBeVisible()
  })

  it('pide propuestas y las presenta sin crear ningún outfit', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = input.toString()
      if (url === '/outfits/proposals') return Promise.resolve(jsonResponse(proposalSet))
      if (isBackgroundList(url)) return Promise.resolve(jsonResponse([]))
      return Promise.resolve(jsonResponse({ detail: 'Ruta inesperada' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)
    renderOutfitApp()

    await screen.findByRole('textbox', { name: 'Describe tu outfit' })
    switchToInspiration()
    fireEvent.change(screen.getByRole('textbox', { name: 'Cuéntame la situación' }), {
      target: { value: 'boda de tarde en octubre en el campo' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Proponer tres outfits' }))

    expect(
      await screen.findByRole('heading', { name: 'Traje de lino arena' }),
    ).toBeVisible()
    expect(fetchMock).toHaveBeenCalledWith('/outfits/proposals', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ situation: 'boda de tarde en octubre en el campo' }),
    })
    // Ninguna llamada de imagen: proponer solo cuesta la llamada de texto.
    expect(
      fetchMock.mock.calls.some(([input]) =>
        input.toString().includes('/regenerate'),
      ),
    ).toBe(false)
    expect(window.localStorage.getItem('active_proposal_id')).toBe('8')
  })

  it('promociona la propuesta elegida al paso de revisión sin generar imagen', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = input.toString()
      if (url === '/outfits/proposals') return Promise.resolve(jsonResponse(proposalSet))
      if (url === '/outfits/proposals/8/choose') {
        return Promise.resolve(jsonResponse(analysis))
      }
      if (isBackgroundList(url)) return Promise.resolve(jsonResponse([]))
      return Promise.resolve(jsonResponse({ detail: 'Ruta inesperada' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)
    renderOutfitApp()

    await screen.findByRole('textbox', { name: 'Describe tu outfit' })
    switchToInspiration()
    fireEvent.change(screen.getByRole('textbox', { name: 'Cuéntame la situación' }), {
      target: { value: 'boda de tarde en octubre en el campo' },
    })
    fireEvent.click(screen.getByRole('button', { name: 'Proponer tres outfits' }))

    fireEvent.click((await screen.findAllByRole('button', { name: 'Elegir esta' }))[1])

    expect(
      await screen.findByRole('heading', { name: 'Prendas interpretadas' }),
    ).toBeVisible()
    expect(fetchMock).toHaveBeenCalledWith('/outfits/proposals/8/choose', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proposal_index: 1 }),
    })
    expect(
      fetchMock.mock.calls.some(([input]) =>
        input.toString().includes('/regenerate'),
      ),
    ).toBe(false)
    expect(window.localStorage.getItem('active_proposal_id')).toBeNull()
    expect(window.localStorage.getItem('active_outfit_id')).toBe('17')
  })

  it('ofrece la otra vía cuando la descripción resulta ser una situación', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = input.toString()
      if (url === '/outfits/generate') {
        return Promise.resolve(
          jsonResponse({
            status: 'needs_clarification',
            message: 'No he reconocido ninguna prenda.',
            suggestion: 'Dime qué prendas quieres ver.',
            suggested_mode: 'inspiration',
          }),
        )
      }
      if (url === '/outfits/proposals') return Promise.resolve(jsonResponse(proposalSet))
      if (isBackgroundList(url)) return Promise.resolve(jsonResponse([]))
      return Promise.resolve(jsonResponse({ detail: 'Ruta inesperada' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)
    renderOutfitApp()

    fireEvent.change(
      await screen.findByRole('textbox', { name: 'Describe tu outfit' }),
      { target: { value: 'boda de tarde en octubre en el campo' } },
    )
    fireEvent.click(screen.getByRole('button', { name: 'Revisar outfit' }))

    fireEvent.click(
      await screen.findByRole('button', {
        name: 'Esto parece un plan: pídeme propuestas',
      }),
    )

    expect(
      await screen.findByRole('heading', { name: 'Traje de lino arena' }),
    ).toBeVisible()
  })

  it('recupera las propuestas ya pagadas al recargar', async () => {
    window.localStorage.setItem('active_proposal_id', '8')
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = input.toString()
      if (url === '/outfits/proposals/8') return Promise.resolve(jsonResponse(proposalSet))
      if (isBackgroundList(url)) return Promise.resolve(jsonResponse([]))
      return Promise.resolve(jsonResponse({ detail: 'Ruta inesperada' }, 404))
    })
    vi.stubGlobal('fetch', fetchMock)
    renderOutfitApp()

    expect(
      await screen.findByRole('heading', { name: 'Chaleco sin americana' }),
    ).toBeVisible()
    expect(fetchMock).toHaveBeenCalledWith('/outfits/proposals/8', undefined)
  })
})
