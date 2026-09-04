import type {
  AnalysisResponse,
  CompletedAnalysis,
  GenerationResponse,
  PersistedOutfit,
  ProductSearchResponse,
  OutfitUpdate,
  ProposalResponse,
  ProposalSet,
  WornViewResponse,
} from '../types/outfit'

const apiBase = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/, '')

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

export async function fetchJson<T>(path: string, options?: RequestInit): Promise<T> {
  let response: Response

  try {
    response = await fetch(`${apiBase}${path}`, options)
  } catch {
    throw new ApiError(
      'No se pudo conectar con el backend. Comprueba que FastAPI está arrancado.',
      0,
    )
  }

  const payload = (await response.json().catch(() => null)) as
    | { detail?: unknown }
    | null

  if (!response.ok) {
    const detail = typeof payload?.detail === 'string' ? payload.detail : null
    throw new ApiError(detail ?? 'El servidor no pudo completar la petición.', response.status)
  }

  return payload as T
}

export function analyzeOutfit(
  userDescription: string,
  replaceOutfitId: number | null = null,
): Promise<AnalysisResponse> {
  return fetchJson<AnalysisResponse>('/outfits/generate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      user_description: userDescription,
      generate_image: false,
      // Reutiliza el análisis que se está editando en vez de dejarlo huérfano.
      replace_outfit_id: replaceOutfitId,
    }),
  })
}

export function proposeOutfits(situation: string): Promise<ProposalResponse> {
  return fetchJson<ProposalResponse>('/outfits/proposals', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ situation }),
  })
}

export function getProposalSet(proposalSetId: number): Promise<ProposalSet> {
  return fetchJson<ProposalSet>(`/outfits/proposals/${proposalSetId}`)
}

// Promocionar una propuesta no llama al proveedor: la extracción ya está guardada.
export function chooseProposal(
  proposalSetId: number,
  proposalIndex: number,
): Promise<CompletedAnalysis> {
  return fetchJson<CompletedAnalysis>(
    `/outfits/proposals/${proposalSetId}/choose`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ proposal_index: proposalIndex }),
    },
  )
}

export function generateOutfitImage(outfitId: number): Promise<GenerationResponse> {
  return fetchJson<GenerationResponse>(`/outfits/${outfitId}/regenerate`, {
    method: 'POST',
  })
}

export function generateWornView(
  outfitId: number,
  imageId: number,
): Promise<WornViewResponse> {
  return fetchJson<WornViewResponse>(
    `/outfits/${outfitId}/images/${imageId}/worn-view`,
    { method: 'POST' },
  )
}

export function getOutfits(
  limit = 24,
  offset = 0,
  favouritesOnly = false,
): Promise<PersistedOutfit[]> {
  // El filtro viaja al backend: aplicarlo aquí solo miraría la página cargada.
  return fetchJson<PersistedOutfit[]>(
    `/outfits?limit=${limit}&offset=${offset}&favourites_only=${favouritesOnly}`,
  )
}

// Portada elegida y favorito. No llama a ningún proveedor: no hay coste ni bloqueo.
export function updateOutfit(
  outfitId: number,
  changes: OutfitUpdate,
): Promise<PersistedOutfit> {
  return fetchJson<PersistedOutfit>(`/outfits/${outfitId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(changes),
  })
}

export function getOutfit(outfitId: number): Promise<PersistedOutfit> {
  return fetchJson<PersistedOutfit>(`/outfits/${outfitId}`)
}

export function deleteOutfit(outfitId: number): Promise<void> {
  return fetchJson<void>(`/outfits/${outfitId}`, { method: 'DELETE' })
}

export function searchOutfitProduct(
  outfitId: number,
  itemIndex: number,
  additionalDetails: string | null,
  forceNew = false,
): Promise<ProductSearchResponse> {
  return fetchJson<ProductSearchResponse>(
    `/outfits/${outfitId}/items/${itemIndex}/product-search`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        additional_details: additionalDetails,
        // Sin esta bandera el backend devuelve la búsqueda guardada sin cobrar.
        force_new: forceNew,
      }),
    },
  )
}

export function resolveImageUrl(path: string): string {
  if (/^(data:|https?:\/\/)/.test(path)) {
    return path
  }
  return `${apiBase}${path}`
}
