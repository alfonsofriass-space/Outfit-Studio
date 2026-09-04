export type OutfitCategory =
  | 'upper'
  | 'lower'
  | 'one_piece'
  | 'footwear'
  | 'accessory'

export type Certainty = 'high' | 'medium' | 'low'

export interface OutfitItem {
  category: OutfitCategory
  item_type: string
  brand: string | null
  color: string | null
  material: string | null
  fit: string | null
  details: string[]
  certainty: Certainty
  visual_phrase_en: string
}

export interface OutfitExtraction {
  status: 'ok' | 'needs_clarification'
  outfit_summary: string
  items: OutfitItem[]
  styling_notes_en: string[]
}

export interface ImageDetails {
  model: string
  quality: string
  size: string
  url_or_base64: string
}

export interface ModelsUsed {
  text_primary: string
  text_fallback: string | null
  image: string | null
}

export interface OutfitAnalysis {
  outfit_id: number
  user_description: string
  outfit: OutfitExtraction
  image_prompt: string | null
  flat_lay_estimated_cost: number
  accessories_omitted: string[]
  product_search_items: ProductSearchItemState[]
}

export interface CompletedAnalysis extends OutfitAnalysis {
  status: 'completed'
  image: ImageDetails | null
  image_id: number | null
  image_error: string | null
  image_prompt: string
  worn_view_preview: WornViewPreview | null
  models_used: ModelsUsed
}

export interface Clarification {
  status: 'needs_clarification'
  message: string
  suggestion: string
  // El backend marca así un texto que parece una situación y no un outfit, para
  // ofrecer la otra vía en vez de un error seco.
  suggested_mode?: 'inspiration' | null
}

export interface OutfitProposal {
  index: number
  title: string
  outfit_summary: string
  items: OutfitItem[]
  styling_notes_en: string[]
}

export interface ProposalSet {
  status: 'proposals_ready'
  proposal_set_id: number
  situation: string
  proposals: OutfitProposal[]
  cost_estimate: number
  // Posiciones que ya tienen outfit: elegir una no cierra las otras dos.
  chosen_indexes: number[]
  created_at: string
  models_used: ModelsUsed
}

export type ProposalResponse = ProposalSet | Clarification

export type AnalysisResponse = CompletedAnalysis | Clarification

export interface WornViewPreview {
  generation_prompt: string
  estimated_cost: number
}

export interface WornViewDetails {
  worn_view_id: number
  source_image_id: number
  generation_prompt: string
  image: ImageDetails
  cost_estimate: number
  created_at: string
}

export interface WornViewResponse {
  status: 'worn_view_ready'
  created: boolean
  outfit_id: number
  source_image_id: number
  worn_view: WornViewDetails
}

export interface GeneratedImage {
  status: 'regenerated'
  outfit_id: number
  image_id: number
  image: ImageDetails
  generation_prompt: string | null
  regeneration_count: number
  regenerations_remaining: number
  worn_view_preview: WornViewPreview | null
}

export interface GenerationLimit {
  status: 'regeneration_limit_reached'
  outfit_id: number
  message: string
  regeneration_count: number
}

export type GenerationResponse = GeneratedImage | GenerationLimit

export interface PersistedOutfitImage {
  image_id: number
  generation_number: number
  generation_prompt: string | null
  image: ImageDetails
  cost_estimate: number
  created_at: string
  worn_view: WornViewDetails | null
}

export interface ProductCandidate {
  title: string
  store: string
  product_url: string
  price_text: string | null
}

export interface ProductSearchDetails {
  item_index: number
  attempt: number
  query: string
  additional_details: string | null
  candidates: ProductCandidate[]
  model: string
  web_search_calls: number
  input_tokens: number
  output_tokens: number
  cost_estimate: number
  created_at: string
}

export interface ProductSearchItemState {
  item_index: number
  query: string | null
  needs_details: boolean
  message: string | null
  estimated_cost: number
  search: ProductSearchDetails | null
  attempts: number
  attempts_remaining: number
}

export interface ProductSearchResponse {
  status: 'product_search_ready'
  created: boolean
  outfit_id: number
  item_index: number
  search: ProductSearchDetails
}

export interface OutfitUpdate {
  // `null` explícito quita la portada elegida; omitir la clave la deja como está.
  chosen_image_id?: number | null
  is_favourite?: boolean
}

export interface PersistedOutfit extends OutfitAnalysis {
  text_model: string
  // Composición marcada como la buena. `null` significa que aún no se ha elegido.
  chosen_image_id: number | null
  is_favourite: boolean
  worn_view_preview: WornViewPreview | null
  images: PersistedOutfitImage[]
  regeneration_count: number
  regenerations_remaining: number
  created_at: string
}
