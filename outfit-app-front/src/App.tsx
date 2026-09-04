import { useCallback, useEffect, useRef, useState } from 'react'
import {
  getCurrentUser,
  login,
  logout,
  register,
  type AuthenticatedUser,
} from './api/auth'
import {
  ApiError,
  analyzeOutfit,
  chooseProposal,
  deleteOutfit,
  generateOutfitImage,
  generateWornView,
  getOutfit,
  getOutfits,
  getProposalSet,
  proposeOutfits,
  searchOutfitProduct,
  updateOutfit,
} from './api/outfits'
import { AnalysisReview } from './components/AnalysisReview'
import { AppSidebar, type AppSection } from './components/AppSidebar'
import { AuthScreen, type AuthMode } from './components/AuthScreen'
import { GenerationResult } from './components/GenerationResult'
import { OutfitComposer, type ComposerMode } from './components/OutfitComposer'
import { OutfitWorkspace } from './components/OutfitWorkspace'
import { ProposalChoice } from './components/ProposalChoice'
import type { ProductSearchAction } from './components/ProductSearchPanel'
import type {
  Clarification,
  GeneratedImage,
  OutfitAnalysis,
  OutfitUpdate,
  PersistedOutfit,
  ProposalSet,
  WornViewDetails,
} from './types/outfit'

type FlowState =
  | { stage: 'draft' }
  | { stage: 'restoring' }
  | { stage: 'analyzing' }
  | { stage: 'clarification'; clarification: Clarification }
  | { stage: 'proposing' }
  | { stage: 'proposals'; proposalSet: ProposalSet }
  | { stage: 'review'; analysis: OutfitAnalysis }
  | { stage: 'generating'; analysis: OutfitAnalysis; previousGeneration?: GeneratedImage }
  | { stage: 'result'; analysis: OutfitAnalysis; generation: GeneratedImage }
  | {
      stage: 'error'
      message: string
      analysis?: OutfitAnalysis
      previousGeneration?: GeneratedImage
    }

type WornViewState =
  | { status: 'idle'; sourceImageId: number }
  | { status: 'loading'; sourceImageId: number }
  | { status: 'ready'; sourceImageId: number; view: WornViewDetails }
  | { status: 'error'; sourceImageId: number; message: string }

type WornViewActionState = Exclude<WornViewState, { status: 'idle' }>

const OUTFITS_PAGE_SIZE = 24
const ACTIVE_OUTFIT_KEY = 'active_outfit_id'
// Las propuestas ya están pagadas: sin este identificador, recargar la página las
// perdería y volver a pedirlas costaría otra llamada.
const ACTIVE_PROPOSAL_KEY = 'active_proposal_id'
const CREATE_STEPS = ['Descripción', 'Revisión', 'Resultado'] as const

function readStoredId(key: string): number | null {
  const value = window.localStorage.getItem(key)
  if (value === null) return null

  const storedId = Number(value)
  if (!Number.isInteger(storedId) || storedId <= 0) {
    window.localStorage.removeItem(key)
    return null
  }
  return storedId
}

function readActiveOutfitId(): number | null {
  return readStoredId(ACTIVE_OUTFIT_KEY)
}

function readActiveProposalId(): number | null {
  return readStoredId(ACTIVE_PROPOSAL_KEY)
}

function rememberActiveProposal(proposalSetId: number) {
  window.localStorage.setItem(ACTIVE_PROPOSAL_KEY, String(proposalSetId))
}

function clearActiveProposal() {
  window.localStorage.removeItem(ACTIVE_PROPOSAL_KEY)
}

function rememberActiveOutfit(outfitId: number) {
  window.localStorage.setItem(ACTIVE_OUTFIT_KEY, String(outfitId))
}

function clearActiveOutfit() {
  window.localStorage.removeItem(ACTIVE_OUTFIT_KEY)
}

function latestPersistedGeneration(outfit: PersistedOutfit): GeneratedImage | null {
  const latest = outfit.images.at(-1)
  if (!latest) return null

  return {
    status: 'regenerated',
    outfit_id: outfit.outfit_id,
    image_id: latest.image_id,
    image: latest.image,
    generation_prompt: latest.generation_prompt,
    regeneration_count: outfit.regeneration_count,
    regenerations_remaining: outfit.regenerations_remaining,
    worn_view_preview: outfit.worn_view_preview,
  }
}

function errorMessage(error: unknown): string {
  return error instanceof Error
    ? error.message
    : 'Ha ocurrido un error inesperado. Inténtalo de nuevo.'
}

interface OutfitAppProps {
  currentUser: AuthenticatedUser
  onLogout: () => Promise<void>
}

export function OutfitApp({ currentUser, onLogout }: OutfitAppProps) {
  const showTechnicalDetails = currentUser.role === 'admin'
  const [activeSection, setActiveSection] = useState<AppSection>('create')
  const hasRenderedInitialSection = useRef(false)
  const [description, setDescription] = useState('')
  const [flow, setFlow] = useState<FlowState>(() =>
    readActiveOutfitId() === null && readActiveProposalId() === null
      ? { stage: 'draft' }
      : { stage: 'restoring' },
  )
  const [composerMode, setComposerMode] = useState<ComposerMode>('describe')
  const [choosingIndex, setChoosingIndex] = useState<number | null>(null)
  const [proposalError, setProposalError] = useState<string | null>(null)
  const [wornViewState, setWornViewState] = useState<WornViewState | null>(null)
  const [archiveWornViewState, setArchiveWornViewState] =
    useState<WornViewActionState | null>(null)
  const [outfits, setOutfits] = useState<PersistedOutfit[]>([])
  const [outfitsLoading, setOutfitsLoading] = useState(true)
  const [outfitsError, setOutfitsError] = useState<string | null>(null)
  const [openingOutfitId, setOpeningOutfitId] = useState<number | null>(null)
  const [deletingOutfitId, setDeletingOutfitId] = useState<number | null>(null)
  const [productSearchAction, setProductSearchAction] =
    useState<ProductSearchAction | null>(null)
  const [sessionError, setSessionError] = useState<string | null>(null)
  // Análisis que el usuario está reescribiendo: al reanalizar se reutiliza su fila
  // en vez de abandonarla como un análisis sin composición en la biblioteca.
  const [editingOutfitId, setEditingOutfitId] = useState<number | null>(null)
  const [favouritesOnly, setFavouritesOnly] = useState(false)
  const [hasMoreOutfits, setHasMoreOutfits] = useState(false)
  const [loadingMoreOutfits, setLoadingMoreOutfits] = useState(false)
  const [updatingOutfitId, setUpdatingOutfitId] = useState<number | null>(null)

  const refreshOutfits = useCallback(async () => {
    setOutfitsLoading(true)
    setOutfitsError(null)
    try {
      const page = await getOutfits(OUTFITS_PAGE_SIZE, 0, favouritesOnly)
      setOutfits(page)
      // Una página llena puede tener continuación; una a medias es el final.
      setHasMoreOutfits(page.length === OUTFITS_PAGE_SIZE)
    } catch (error) {
      setOutfitsError(errorMessage(error))
    } finally {
      setOutfitsLoading(false)
    }
  }, [favouritesOnly])

  const loadMoreOutfits = useCallback(async () => {
    setLoadingMoreOutfits(true)
    setOutfitsError(null)
    try {
      const page = await getOutfits(
        OUTFITS_PAGE_SIZE,
        outfits.length,
        favouritesOnly,
      )
      setOutfits((current) => [...current, ...page])
      setHasMoreOutfits(page.length === OUTFITS_PAGE_SIZE)
    } catch (error) {
      setOutfitsError(errorMessage(error))
    } finally {
      setLoadingMoreOutfits(false)
    }
  }, [favouritesOnly, outfits.length])

  const handleUpdateOutfit = useCallback(
    async (outfitId: number, changes: OutfitUpdate) => {
      setUpdatingOutfitId(outfitId)
      setOutfitsError(null)
      try {
        const updated = await updateOutfit(outfitId, changes)
        setOutfits((current) =>
          current.map((outfit) =>
            outfit.outfit_id === outfitId ? updated : outfit,
          ),
        )
      } catch (error) {
        setOutfitsError(errorMessage(error))
      } finally {
        setUpdatingOutfitId(null)
      }
    },
    [],
  )

  const activatePersistedOutfit = useCallback((outfit: PersistedOutfit) => {
    const generation = latestPersistedGeneration(outfit)
    const latestImage = outfit.images.at(-1)

    rememberActiveOutfit(outfit.outfit_id)
    setDescription(outfit.user_description)
    if (!generation || !latestImage) {
      setWornViewState(null)
      setFlow({ stage: 'review', analysis: outfit })
      return
    }

    if (latestImage.worn_view) {
      setWornViewState({
        status: 'ready',
        sourceImageId: latestImage.image_id,
        view: latestImage.worn_view,
      })
    } else if (outfit.worn_view_preview) {
      setWornViewState({ status: 'idle', sourceImageId: latestImage.image_id })
    } else {
      setWornViewState(null)
    }
    setFlow({ stage: 'result', analysis: outfit, generation })
  }, [])

  useEffect(() => {
    void refreshOutfits()
  }, [refreshOutfits])

  useEffect(() => {
    if (!hasRenderedInitialSection.current) {
      hasRenderedInitialSection.current = true
      return
    }

    const headingId =
      activeSection === 'create' ? 'create-view-title' : 'archive-title'
    document.getElementById(headingId)?.focus()
  }, [activeSection])

  useEffect(() => {
    const outfitId = readActiveOutfitId()
    if (outfitId === null) {
      // Sin outfit activo puede quedar un conjunto de propuestas ya pagado.
      const proposalSetId = readActiveProposalId()
      if (proposalSetId === null) return

      let cancelledProposal = false
      void getProposalSet(proposalSetId)
        .then((proposalSet) => {
          if (!cancelledProposal) setFlow({ stage: 'proposals', proposalSet })
        })
        .catch((error: unknown) => {
          if (cancelledProposal) return
          clearActiveProposal()
          if (error instanceof ApiError && error.status === 404) {
            setFlow({ stage: 'draft' })
            return
          }
          setFlow({ stage: 'error', message: errorMessage(error) })
        })

      return () => {
        cancelledProposal = true
      }
    }

    let cancelled = false
    void getOutfit(outfitId)
      .then((outfit) => {
        if (!cancelled) activatePersistedOutfit(outfit)
      })
      .catch((error: unknown) => {
        if (cancelled) return
        if (error instanceof ApiError && error.status === 404) {
          clearActiveOutfit()
          setFlow({ stage: 'draft' })
          return
        }
        setFlow({ stage: 'error', message: errorMessage(error) })
      })

    return () => {
      cancelled = true
    }
  }, [activatePersistedOutfit])

  const handleOpenOutfit = async (outfitId: number) => {
    setOpeningOutfitId(outfitId)
    setOutfitsError(null)
    try {
      activatePersistedOutfit(await getOutfit(outfitId))
      setActiveSection('create')
      document.getElementById('top')?.scrollIntoView?.({
        behavior: 'smooth',
        block: 'start',
      })
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        clearActiveOutfit()
      }
      setOutfitsError(errorMessage(error))
    } finally {
      setOpeningOutfitId(null)
    }
  }

  const handleAnalyze = async () => {
    const normalizedDescription = description.trim()
    if (!normalizedDescription) return

    clearActiveOutfit()
    setWornViewState(null)
    setFlow({ stage: 'analyzing' })
    try {
      const response = await analyzeOutfit(normalizedDescription, editingOutfitId)
      if (response.status === 'needs_clarification') {
        setFlow({ stage: 'clarification', clarification: response })
        return
      }
      setEditingOutfitId(null)
      rememberActiveOutfit(response.outfit_id)
      setFlow({ stage: 'review', analysis: response })
      void refreshOutfits()
    } catch (error) {
      setFlow({ stage: 'error', message: errorMessage(error) })
    }
  }

  const handlePropose = async () => {
    const normalizedSituation = description.trim()
    if (!normalizedSituation) return

    clearActiveOutfit()
    clearActiveProposal()
    setWornViewState(null)
    setProposalError(null)
    setFlow({ stage: 'proposing' })
    try {
      const response = await proposeOutfits(normalizedSituation)
      if (response.status === 'needs_clarification') {
        setFlow({ stage: 'clarification', clarification: response })
        return
      }
      rememberActiveProposal(response.proposal_set_id)
      setFlow({ stage: 'proposals', proposalSet: response })
    } catch (error) {
      setFlow({ stage: 'error', message: errorMessage(error) })
    }
  }

  const handleSubmitComposer = () =>
    composerMode === 'inspiration' ? void handlePropose() : void handleAnalyze()

  // Promocionar no llama al proveedor: la extracción de la propuesta ya está
  // guardada, así que un fallo aquí no puede haber gastado nada.
  const handleChooseProposal = async (
    proposalSet: ProposalSet,
    proposalIndex: number,
  ) => {
    setChoosingIndex(proposalIndex)
    setProposalError(null)
    try {
      const analysis = await chooseProposal(
        proposalSet.proposal_set_id,
        proposalIndex,
      )
      clearActiveProposal()
      rememberActiveOutfit(analysis.outfit_id)
      setDescription(analysis.user_description)
      setEditingOutfitId(null)
      setFlow({ stage: 'review', analysis })
      void refreshOutfits()
    } catch (error) {
      setProposalError(errorMessage(error))
    } finally {
      setChoosingIndex(null)
    }
  }

  const handleGenerate = async (
    analysis: OutfitAnalysis,
    previousGeneration?: GeneratedImage,
  ) => {
    setWornViewState(null)
    setFlow({ stage: 'generating', analysis, previousGeneration })
    try {
      const response = await generateOutfitImage(analysis.outfit_id)
      if (response.status === 'regeneration_limit_reached') {
        throw new Error(response.message)
      }
      setFlow({ stage: 'result', analysis, generation: response })
      if (response.worn_view_preview) {
        setWornViewState({ status: 'idle', sourceImageId: response.image_id })
      }
      void refreshOutfits()
    } catch (error) {
      setFlow({
        stage: 'error',
        message: errorMessage(error),
        analysis,
        previousGeneration,
      })
    }
  }

  const generateWornViewWithState = async (
    outfitId: number,
    imageId: number,
    setState: (state: WornViewActionState) => void,
  ) => {
    setState({ status: 'loading', sourceImageId: imageId })
    try {
      const response = await generateWornView(outfitId, imageId)
      setState({
        status: 'ready',
        sourceImageId: imageId,
        view: response.worn_view,
      })
      void refreshOutfits()
    } catch (error) {
      setState({
        status: 'error',
        sourceImageId: imageId,
        message: errorMessage(error),
      })
    }
  }

  const handleGenerateActiveWornView = (outfitId: number, imageId: number) =>
    generateWornViewWithState(outfitId, imageId, (state) =>
      setWornViewState(state),
    )

  const handleGenerateArchiveWornView = (outfitId: number, imageId: number) =>
    generateWornViewWithState(outfitId, imageId, (state) =>
      setArchiveWornViewState(state),
    )

  const isImageOperationLocked =
    flow.stage === 'generating' ||
    wornViewState?.status === 'loading' ||
    archiveWornViewState?.status === 'loading'
  const isProductSearchLoading = productSearchAction?.status === 'loading'
  const isPaidOperationLocked =
    isImageOperationLocked || isProductSearchLoading

  const isGlobalResetLocked =
    flow.stage === 'restoring' ||
    flow.stage === 'analyzing' ||
    flow.stage === 'proposing' ||
    choosingIndex !== null ||
    isPaidOperationLocked ||
    openingOutfitId !== null ||
    deletingOutfitId !== null

  const startAgain = () => {
    clearActiveOutfit()
    clearActiveProposal()
    setProposalError(null)
    setDescription('')
    setWornViewState(null)
    setArchiveWornViewState(null)
    setEditingOutfitId(null)
    setFlow({ stage: 'draft' })
  }

  const handleNavigate = (section: AppSection) => {
    setActiveSection(section)
    document.getElementById('top')?.scrollIntoView?.({
      behavior: 'smooth',
      block: 'start',
    })
  }

  const handleStartNewOutfit = () => {
    if (isGlobalResetLocked) return

    startAgain()
    handleNavigate('create')
  }

  const handleLogout = async () => {
    // Salir no se bloquea durante una operación de pago: la reserva y la
    // persistencia viven en el servidor, así que la generación termina y se
    // guarda igual. Dejar a alguien sin poder cerrar sesión no protege nada.
    setSessionError(null)
    try {
      await onLogout()
    } catch (error) {
      setSessionError(errorMessage(error))
    }
  }

  const handleDeleteOutfit = async (outfitId: number) => {
    setDeletingOutfitId(outfitId)
    setOutfitsError(null)
    try {
      await deleteOutfit(outfitId)
      setOutfits((currentOutfits) =>
        currentOutfits.filter((outfit) => outfit.outfit_id !== outfitId),
      )
      setArchiveWornViewState(null)
      if (productSearchAction?.outfitId === outfitId) {
        setProductSearchAction(null)
      }
      if (readActiveOutfitId() === outfitId) {
        startAgain()
        setActiveSection('create')
      }
    } catch (error) {
      setOutfitsError(errorMessage(error))
    } finally {
      setDeletingOutfitId(null)
    }
  }

  const handleSearchProduct = async (
    outfitId: number,
    itemIndex: number,
    additionalDetails: string | null,
    forceNew: boolean,
  ) => {
    setProductSearchAction({
      status: 'loading',
      outfitId,
      itemIndex,
    })
    try {
      const response = await searchOutfitProduct(
        outfitId,
        itemIndex,
        additionalDetails,
        forceNew,
      )
      setOutfits((currentOutfits) =>
        currentOutfits.map((outfit) =>
          outfit.outfit_id === outfitId
            ? {
                ...outfit,
                product_search_items: outfit.product_search_items.map((item) =>
                  item.item_index === response.item_index
                    ? {
                        ...item,
                        query: response.search.query,
                        needs_details: false,
                        message: null,
                        search: response.search,
                        attempts: response.search.attempt,
                        attempts_remaining: Math.max(
                          item.attempts_remaining - (response.created ? 1 : 0),
                          0,
                        ),
                      }
                    : item,
                ),
              }
            : outfit,
        ),
      )
      // El análisis activo del flujo de creación tiene su propia copia del estado;
      // sin esto el panel seguiría mostrando la búsqueda anterior.
      setFlow((current) => {
        const analysis = 'analysis' in current ? current.analysis : undefined
        if (!analysis || analysis.outfit_id !== outfitId) return current
        return {
          ...current,
          analysis: {
            ...analysis,
            product_search_items: analysis.product_search_items.map((item) =>
              item.item_index === response.item_index
                ? {
                    ...item,
                    query: response.search.query,
                    needs_details: false,
                    message: null,
                    search: response.search,
                    attempts: response.search.attempt,
                    attempts_remaining: Math.max(
                      item.attempts_remaining - (response.created ? 1 : 0),
                      0,
                    ),
                  }
                : item,
            ),
          },
        } as FlowState
      })
      setProductSearchAction(null)
    } catch (error) {
      setProductSearchAction({
        status: 'error',
        outfitId,
        itemIndex,
        message: errorMessage(error),
      })
    }
  }

  const analysisForReview =
    flow.stage === 'review' || (flow.stage === 'generating' && !flow.previousGeneration)
      ? flow.analysis
      : flow.stage === 'error' && !flow.previousGeneration
        ? flow.analysis
        : undefined

  const resultState =
    flow.stage === 'result'
      ? {
          analysis: flow.analysis,
          generation: flow.generation,
          isGenerating: false,
          variationError: null,
        }
      : flow.stage === 'generating' && flow.previousGeneration
        ? {
            analysis: flow.analysis,
            generation: flow.previousGeneration,
            isGenerating: true,
            variationError: null,
          }
        : flow.stage === 'error' && flow.analysis && flow.previousGeneration
          ? {
              analysis: flow.analysis,
              generation: flow.previousGeneration,
              isGenerating: false,
              variationError: flow.message,
            }
          : undefined

  const activeWornViewState =
    resultState && wornViewState?.sourceImageId === resultState.generation.image_id
      ? wornViewState
      : null

  const isComposerStage =
    flow.stage === 'draft' ||
    flow.stage === 'analyzing' ||
    flow.stage === 'proposing' ||
    flow.stage === 'clarification' ||
    (flow.stage === 'error' && !flow.analysis)
  const proposalState = flow.stage === 'proposals' ? flow.proposalSet : null
  const currentCreateStep = resultState ? 3 : analysisForReview ? 2 : 1

  const heroCopy =
    currentCreateStep === 3
      ? {
          eyebrow: 'Outfit generado · Paso 3 de 3',
          title: <>Tu composición <em>está lista.</em></>,
        }
      : currentCreateStep === 2
        ? {
            eyebrow: 'Paso 2 de 3',
            title: <>Comprueba antes de <em>generar.</em></>,
          }
        : proposalState
          ? {
              eyebrow: 'Tres propuestas',
              title: <>Elige la que <em>te convenza.</em></>,
            }
          : composerMode === 'inspiration'
            ? {
                eyebrow: 'Tu próximo look',
                title: <>Cuéntame <em>tu plan.</em></>,
              }
            : {
                eyebrow: 'Tu próximo look',
                title: <>Describe lo que <em>tienes en mente.</em></>,
              }

  const heroStatus =
    currentCreateStep === 3
      ? resultState
        ? `${resultState.generation.regenerations_remaining} variaciones disponibles`
        : null
      : currentCreateStep === 2
        ? flow.stage === 'error'
          ? 'Listo para reintentar'
          : 'Sin imagen todavía'
        : null

  const operationLabel =
    flow.stage === 'restoring'
      ? 'Recuperando outfit…'
      : flow.stage === 'analyzing'
        ? 'Analizando…'
        : flow.stage === 'proposing'
          ? 'Proponiendo…'
        : flow.stage === 'generating'
          ? 'Generando composición…'
          : wornViewState?.status === 'loading' ||
              archiveWornViewState?.status === 'loading'
            ? 'Generando vista puesta…'
            : productSearchAction?.status === 'loading'
              ? 'Buscando productos…'
              : openingOutfitId !== null
                ? 'Abriendo outfit…'
                : deletingOutfitId !== null
                  ? 'Eliminando outfit…'
                  : null

  return (
    <div className="app-shell">
      <AppSidebar
        activeSection={activeSection}
        outfitCount={outfits.length}
        operationLabel={operationLabel}
        isNewOutfitDisabled={isGlobalResetLocked}
        currentUser={currentUser}
        sessionError={sessionError}
        onNavigate={handleNavigate}
        onStartNew={handleStartNewOutfit}
        onLogout={() => void handleLogout()}
      />

      <div className="app-content">
        <main id="top">
          {activeSection === 'create' ? (
            <div className="create-view">
              <section
                className={
                  isComposerStage
                    ? 'hero create-hero create-hero--intro'
                    : 'hero create-hero'
                }
              >
                <div className="create-hero__lead">
                  <p className="eyebrow">{heroCopy.eyebrow}</p>
                  <h1 id="create-view-title" tabIndex={-1}>
                    {heroCopy.title}
                  </h1>
                  {isComposerStage && (
                    <p className="hero__intro">
                      {composerMode === 'inspiration'
                        ? 'Primero elegirás entre tres propuestas y podrás revisarla. La imagen solo se genera cuando tú la confirmas.'
                        : showTechnicalDetails
                          ? 'Primero podrás comprobar las prendas y el prompt. La imagen solo se genera cuando tú la confirmas.'
                          : 'Primero podrás comprobar las prendas interpretadas. La imagen solo se genera cuando tú la confirmas.'}
                    </p>
                  )}
                </div>
                <div className="create-hero__aside">
                  {heroStatus && (
                    <span className="status-pill">{heroStatus}</span>
                  )}
                  <ol className="steps" aria-label="Proceso de generación">
                  {CREATE_STEPS.map((label, index) => {
                    const step = index + 1
                    const stateClass =
                      step === currentCreateStep
                        ? 'is-active'
                        : step < currentCreateStep
                          ? 'is-complete'
                          : ''

                    return (
                      <li
                        className={stateClass}
                        key={label}
                        aria-current={
                          step === currentCreateStep ? 'step' : undefined
                        }
                      >
                        <span className="steps__number" aria-hidden="true">
                          {step < currentCreateStep ? '✓' : `0${step}`}
                        </span>
                        <span className="steps__label">{label}</span>
                      </li>
                    )
                    })}
                  </ol>
                </div>
              </section>

              <section
                className={
                  isComposerStage
                    ? 'workspace workspace--composer'
                    : 'workspace'
                }
                aria-label="Generador de outfits"
              >
                {flow.stage === 'restoring' && (
                  <div className="generation-status" role="status">
                    <strong>Recuperando el outfit activo…</strong>
                    <span>Cargando su análisis y sus imágenes guardadas.</span>
                  </div>
                )}

                {isComposerStage && (
                  <>
                    {(flow.stage === 'clarification' || flow.stage === 'error') && (
                      <div className="notice" role="alert">
                        <strong>
                          {flow.stage === 'clarification'
                            ? flow.clarification.message
                            : flow.message}
                        </strong>
                        {flow.stage === 'clarification' && (
                          <span>{flow.clarification.suggestion}</span>
                        )}
                        {flow.stage === 'clarification' &&
                          flow.clarification.suggested_mode === 'inspiration' && (
                            <button
                              className="button button--secondary"
                              type="button"
                              onClick={() => {
                                setComposerMode('inspiration')
                                void handlePropose()
                              }}
                            >
                              Esto parece un plan: pídeme propuestas
                            </button>
                          )}
                      </div>
                    )}
                    <OutfitComposer
                      description={description}
                      isLoading={
                        flow.stage === 'analyzing' || flow.stage === 'proposing'
                      }
                      mode={composerMode}
                      onChange={setDescription}
                      onModeChange={(mode) => {
                        setComposerMode(mode)
                        if (flow.stage === 'clarification') setFlow({ stage: 'draft' })
                      }}
                      onSubmit={handleSubmitComposer}
                    />
                  </>
                )}

                {proposalState && (
                  <ProposalChoice
                    choosingIndex={choosingIndex}
                    errorMessage={proposalError}
                    proposalSet={proposalState}
                    showTechnicalDetails={showTechnicalDetails}
                    onChoose={(proposalIndex) =>
                      void handleChooseProposal(proposalState, proposalIndex)
                    }
                    onRestart={startAgain}
                  />
                )}

                {flow.stage === 'error' &&
                  flow.analysis &&
                  !flow.previousGeneration && (
                    <div className="notice" role="alert">
                      <strong>{flow.message}</strong>
                      <span>
                        El análisis sigue guardado. No se ha creado una imagen ni se
                        ha consumido una regeneración.
                      </span>
                    </div>
                  )}

                {analysisForReview && (
                  <AnalysisReview
                    analysis={analysisForReview}
                    isGenerating={flow.stage === 'generating'}
                    isImageOperationLocked={isPaidOperationLocked}
                    isRetry={flow.stage === 'error'}
                    showTechnicalDetails={showTechnicalDetails}
                    onEdit={() => {
                      clearActiveOutfit()
                      setWornViewState(null)
                      // Se conserva el id: al reanalizar se reescribe esta misma
                      // fila y no queda un análisis huérfano en la biblioteca.
                      setEditingOutfitId(analysisForReview.outfit_id)
                      setFlow({ stage: 'draft' })
                    }}
                    onGenerate={() => void handleGenerate(analysisForReview)}
                  />
                )}

                {resultState && (
                  <GenerationResult
                    analysis={resultState.analysis}
                    generation={resultState.generation}
                    isGeneratingVariation={resultState.isGenerating}
                    variationError={resultState.variationError}
                    wornView={
                      activeWornViewState?.status === 'ready'
                        ? activeWornViewState.view
                        : null
                    }
                    isGeneratingWornView={
                      activeWornViewState?.status === 'loading'
                    }
                    isImageOperationLocked={isPaidOperationLocked}
                    showTechnicalDetails={showTechnicalDetails}
                    wornViewError={
                      activeWornViewState?.status === 'error'
                        ? activeWornViewState.message
                        : null
                    }
                    productSearchAction={productSearchAction}
                    onSearchProduct={(
                      outfitId,
                      itemIndex,
                      additionalDetails,
                      forceNew,
                    ) =>
                      void handleSearchProduct(
                        outfitId,
                        itemIndex,
                        additionalDetails,
                        forceNew,
                      )
                    }
                    onGenerateWornView={() =>
                      void handleGenerateActiveWornView(
                        resultState.analysis.outfit_id,
                        resultState.generation.image_id,
                      )
                    }
                    onGenerateVariation={() =>
                      void handleGenerate(
                        resultState.analysis,
                        resultState.generation,
                      )
                    }
                    onStartAgain={handleStartNewOutfit}
                  />
                )}
              </section>

              {showTechnicalDetails && (
                <div className="spend-explainer">
                  <span className="spend-explainer__icon" aria-hidden="true">
                    ✓
                  </span>
                  <p>
                    <strong>Control de gasto:</strong> revisar realiza una llamada de
                    texto de coste bajo. Cada generación visual muestra su estimación
                    y exige una acción explícita antes de empezar.
                  </p>
                </div>
              )}
            </div>
          ) : (
            <div className="library-view">
              <OutfitWorkspace
                outfits={outfits}
                isLoading={outfitsLoading}
                openingOutfitId={openingOutfitId}
                deletingOutfitId={deletingOutfitId}
                wornViewAction={archiveWornViewState}
                productSearchAction={productSearchAction}
                hasMoreOutfits={hasMoreOutfits}
                isLoadingMore={loadingMoreOutfits}
                favouritesOnly={favouritesOnly}
                updatingOutfitId={updatingOutfitId}
                onLoadMore={() => void loadMoreOutfits()}
                onToggleFavouritesOnly={setFavouritesOnly}
                onUpdateOutfit={(outfitId, changes) =>
                  void handleUpdateOutfit(outfitId, changes)
                }
                isPaidOperationLocked={isPaidOperationLocked}
                showTechnicalDetails={showTechnicalDetails}
                error={outfitsError}
                onContinue={(outfitId) => void handleOpenOutfit(outfitId)}
                onDelete={(outfitId) => void handleDeleteOutfit(outfitId)}
                onGenerateWornView={(outfitId, imageId) =>
                  void handleGenerateArchiveWornView(outfitId, imageId)
                }
                onSearchProduct={(outfitId, itemIndex, additionalDetails, forceNew) =>
                  void handleSearchProduct(
                    outfitId,
                    itemIndex,
                    additionalDetails,
                    forceNew,
                  )
                }
                onRetry={() => void refreshOutfits()}
              />
            </div>
          )}
        </main>

        <footer className="site-footer">
          <span>Outfit Studio</span>
          <span>
            {showTechnicalDetails
              ? 'Herramienta local de uso personal'
              : 'Tu espacio personal de outfits'}
          </span>
        </footer>
      </div>
    </div>
  )
}

type SessionState =
  | { status: 'loading' }
  | { status: 'anonymous'; error: string | null; isSubmitting: boolean }
  | { status: 'authenticated'; user: AuthenticatedUser }

function App() {
  const [session, setSession] = useState<SessionState>({ status: 'loading' })

  useEffect(() => {
    let cancelled = false
    void getCurrentUser()
      .then((user) => {
        if (!cancelled) setSession({ status: 'authenticated', user })
      })
      .catch((error: unknown) => {
        if (cancelled) return
        const message =
          error instanceof ApiError && error.status === 401 ? null : errorMessage(error)
        setSession({ status: 'anonymous', error: message, isSubmitting: false })
      })

    return () => {
      cancelled = true
    }
  }, [])

  const authenticate = async (mode: AuthMode, username: string, password: string) => {
    setSession({ status: 'anonymous', error: null, isSubmitting: true })
    try {
      const user = await (mode === 'login'
        ? login({ username, password })
        : register({ username, password }))
      clearActiveOutfit()
      setSession({ status: 'authenticated', user })
    } catch (error) {
      setSession({
        status: 'anonymous',
        error: errorMessage(error),
        isSubmitting: false,
      })
    }
  }

  const endSession = async () => {
    await logout()
    clearActiveOutfit()
    setSession({ status: 'anonymous', error: null, isSubmitting: false })
  }

  if (session.status === 'loading') {
    return (
      <main className="session-loading" role="status">
        <span className="brand__mark" aria-hidden="true">
          ✦
        </span>
        <strong>Abriendo Outfit Studio…</strong>
      </main>
    )
  }

  if (session.status === 'anonymous') {
    return (
      <AuthScreen
        isSubmitting={session.isSubmitting}
        error={session.error}
        onSubmit={(mode, username, password) =>
          void authenticate(mode, username, password)
        }
      />
    )
  }

  return <OutfitApp currentUser={session.user} onLogout={endSession} />
}

export default App
