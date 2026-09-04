import { useEffect, useRef, useState } from 'react'
import type { FormEvent, KeyboardEvent } from 'react'

const MAX_DESCRIPTION_LENGTH = 500

interface SpeechRecognitionAlternativeLike {
  transcript: string
}

interface SpeechRecognitionResultLike {
  isFinal: boolean
  length: number
  [index: number]: SpeechRecognitionAlternativeLike
}

interface SpeechRecognitionResultListLike {
  length: number
  [index: number]: SpeechRecognitionResultLike
}

interface SpeechRecognitionEventLike {
  resultIndex: number
  results: SpeechRecognitionResultListLike
}

interface SpeechRecognitionErrorEventLike {
  error: string
}

interface BrowserSpeechRecognition {
  continuous: boolean
  interimResults: boolean
  lang: string
  maxAlternatives: number
  onresult: ((event: SpeechRecognitionEventLike) => void) | null
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null
  onend: (() => void) | null
  start: () => void
  stop: () => void
  abort: () => void
}

type BrowserSpeechRecognitionConstructor = new () => BrowserSpeechRecognition

type SpeechRecognitionWindow = Window & {
  SpeechRecognition?: BrowserSpeechRecognitionConstructor
  webkitSpeechRecognition?: BrowserSpeechRecognitionConstructor
}

function speechRecognitionConstructor(): BrowserSpeechRecognitionConstructor | null {
  if (typeof window === 'undefined') return null
  const speechWindow = window as SpeechRecognitionWindow
  return speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition ?? null
}

function appendTranscript(current: string, transcript: string): string {
  const base = current.trimEnd()
  const addition = transcript.trim().replace(/\s+/g, ' ')
  if (!addition) return current
  return base ? `${base} ${addition}` : addition
}

export type ComposerMode = 'describe' | 'inspiration'

// Las dos vías comparten campo, dictado y geometría; solo cambia lo que se pide y
// lo que se promete. La copia de `describe` es la que ya existía y no se toca.
const MODE_COPY: Record<
  ComposerMode,
  {
    option: string
    label: string
    help: string
    placeholder: string
    submit: string
    loading: string
    hint: string
  }
> = {
  describe: {
    option: 'Sé lo que quiero',
    label: 'Describe tu outfit',
    help: 'Incluye prendas, colores, materiales, relaciones y ocasión. Revisaremos la interpretación antes de generar.',
    placeholder:
      'Ej.: camisa blanca de lino, pantalón sastre gris de pierna ancha y mocasines negros de piel para una presentación de trabajo en verano.',
    submit: 'Revisar outfit',
    loading: 'Analizando…',
    hint: 'Intro para revisar · Mayús + Intro para una nueva línea',
  },
  inspiration: {
    option: 'Inspírame',
    label: 'Cuéntame la situación',
    help: 'Dime la ocasión, el lugar y la época del año. Te propondré tres outfits completos y eliges uno.',
    placeholder:
      'Ej.: boda de tarde en octubre, en el campo, voy de invitado y no quiero ir de traje entero.',
    submit: 'Proponer tres outfits',
    loading: 'Proponiendo…',
    hint: 'Intro para pedir propuestas · Mayús + Intro para una nueva línea',
  },
}

interface OutfitComposerProps {
  description: string
  isLoading: boolean
  mode: ComposerMode
  onChange: (value: string) => void
  onModeChange: (mode: ComposerMode) => void
  onSubmit: () => void
}

export function OutfitComposer({
  description,
  isLoading,
  mode,
  onChange,
  onModeChange,
  onSubmit,
}: OutfitComposerProps) {
  const copy = MODE_COPY[mode]
  const [recognition, setRecognition] = useState<BrowserSpeechRecognition | null>(
    null,
  )
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const isListening = recognition !== null
  const supportsSpeech = speechRecognitionConstructor() !== null
  const canSubmit = description.trim().length > 0 && !isLoading && !isListening

  useEffect(() => {
    if (!recognition) return

    return () => {
      recognition.onresult = null
      recognition.onerror = null
      recognition.onend = null
      recognition.abort()
    }
  }, [recognition])

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (canSubmit) onSubmit()
  }

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      if (canSubmit) onSubmit()
    }
  }

  const toggleDictation = () => {
    if (recognition) {
      recognition.stop()
      return
    }

    const Recognition = speechRecognitionConstructor()
    if (!Recognition) return

    const nextRecognition = new Recognition()
    let dictatedText = ''

    nextRecognition.lang = 'es-ES'
    nextRecognition.continuous = true
    nextRecognition.interimResults = false
    nextRecognition.maxAlternatives = 1

    nextRecognition.onresult = (event) => {
      let newTranscript = ''
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const result = event.results[index]
        if (result.isFinal && result.length > 0) {
          newTranscript = appendTranscript(newTranscript, result[0].transcript)
        }
      }
      if (!newTranscript) return

      dictatedText = appendTranscript(dictatedText, newTranscript)
      const combinedDescription = appendTranscript(description, dictatedText)
      if (combinedDescription.length > MAX_DESCRIPTION_LENGTH) {
        onChange(combinedDescription.slice(0, MAX_DESCRIPTION_LENGTH))
        nextRecognition.stop()
        return
      }
      onChange(combinedDescription)
    }

    nextRecognition.onerror = (event) => {
      if (event.error === 'aborted') return
      setRecognition(null)
      textareaRef.current?.focus()
    }

    nextRecognition.onend = () => {
      setRecognition(null)
      textareaRef.current?.focus()
    }

    setRecognition(nextRecognition)
    try {
      nextRecognition.start()
    } catch {
      setRecognition(null)
    }
  }

  const speechUnavailableMessage = supportsSpeech
    ? null
    : 'El dictado por voz no está disponible en este navegador.'

  return (
    <form className="composer" onSubmit={submit}>
      <fieldset className="composer__modes" disabled={isLoading || isListening}>
        <legend>Modo de entrada</legend>
        {(Object.keys(MODE_COPY) as ComposerMode[]).map((option) => (
          <label
            className={
              option === mode
                ? 'composer__mode composer__mode--active'
                : 'composer__mode'
            }
            key={option}
          >
            <input
              checked={option === mode}
              name="composer-mode"
              type="radio"
              value={option}
              onChange={() => onModeChange(option)}
            />
            {MODE_COPY[option].option}
          </label>
        ))}
      </fieldset>
      <div className="composer__heading">
        <label htmlFor="outfit-description">{copy.label}</label>
        <p>{copy.help}</p>
      </div>
      <div className="composer__box">
        <textarea
          ref={textareaRef}
          id="outfit-description"
          value={description}
          maxLength={MAX_DESCRIPTION_LENGTH}
          rows={9}
          placeholder={copy.placeholder}
          disabled={isLoading}
          readOnly={isListening}
          aria-describedby={
            speechUnavailableMessage
              ? 'outfit-description-hint outfit-speech-feedback'
              : 'outfit-description-hint'
          }
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={handleKeyDown}
        />
        <div className="composer__footer">
          <span>{description.length}/500</span>
          <div className="composer__actions">
            <button
              className={
                isListening
                  ? 'composer__voice composer__voice--listening'
                  : 'composer__voice'
              }
              type="button"
              disabled={isLoading || !supportsSpeech}
              aria-pressed={isListening}
              aria-label={
                isListening ? 'Detener dictado por voz' : 'Iniciar dictado por voz'
              }
              onClick={toggleDictation}
            >
              <svg aria-hidden="true" viewBox="0 0 20 20">
                <rect x="7" y="2.5" width="6" height="10" rx="3" />
                <path d="M4.5 9.5a5.5 5.5 0 0 0 11 0M10 15v2.5M7.5 17.5h5" />
              </svg>
            </button>
            <button
              className="button button--primary"
              type="submit"
              disabled={!canSubmit}
            >
              {isLoading ? (
                copy.loading
              ) : (
                <>
                  {copy.submit} <span aria-hidden="true">→</span>
                </>
              )}
            </button>
          </div>
        </div>
      </div>
      {speechUnavailableMessage && (
        <p
          id="outfit-speech-feedback"
          className="composer__speech-feedback"
          role="status"
        >
          {speechUnavailableMessage}
        </p>
      )}
      <p id="outfit-description-hint" className="composer__hint">
        {copy.hint}
      </p>
    </form>
  )
}
