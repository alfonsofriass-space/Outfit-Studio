import { useState } from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { OutfitComposer } from './OutfitComposer'

interface FakeSpeechResult {
  isFinal: boolean
  length: number
  [index: number]: { transcript: string }
}

interface FakeSpeechResults {
  length: number
  [index: number]: FakeSpeechResult
}

class FakeSpeechRecognition {
  static latest: FakeSpeechRecognition | null = null

  continuous = false
  interimResults = false
  lang = ''
  maxAlternatives = 0
  onresult:
    | ((event: { resultIndex: number; results: FakeSpeechResults }) => void)
    | null = null
  onerror: ((event: { error: string }) => void) | null = null
  onend: (() => void) | null = null
  start = vi.fn()
  stop = vi.fn(() => this.onend?.())
  abort = vi.fn()

  constructor() {
    FakeSpeechRecognition.latest = this
  }

  emitFinal(transcript: string) {
    this.onresult?.({
      resultIndex: 0,
      results: {
        0: {
          0: { transcript },
          isFinal: true,
          length: 1,
        },
        length: 1,
      },
    })
  }

  emitError(error: string) {
    this.onerror?.({ error })
  }
}

function installSpeechRecognition() {
  Object.defineProperty(window, 'SpeechRecognition', {
    configurable: true,
    value: FakeSpeechRecognition,
  })
}

afterEach(() => {
  Reflect.deleteProperty(window, 'SpeechRecognition')
  FakeSpeechRecognition.latest = null
})

describe('OutfitComposer', () => {
  it('expone las dos vías como radios y avisa del cambio', () => {
    const onModeChange = vi.fn()

    render(
      <OutfitComposer
        description="camisa blanca"
        isLoading={false}
        mode="describe"
        onChange={vi.fn()}
        onModeChange={onModeChange}
        onSubmit={vi.fn()}
      />,
    )

    expect(screen.getByRole('radio', { name: 'Sé lo que quiero' })).toBeChecked()
    expect(screen.getByRole('radio', { name: 'Inspírame' })).not.toBeChecked()

    fireEvent.click(screen.getByRole('radio', { name: 'Inspírame' }))

    expect(onModeChange).toHaveBeenCalledWith('inspiration')
  })

  it('no deja cambiar de vía mientras hay una llamada en curso', () => {
    render(
      <OutfitComposer
        description="boda de tarde en octubre"
        isLoading
        mode="inspiration"
        onChange={vi.fn()}
        onModeChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    )

    expect(screen.getByRole('radio', { name: 'Sé lo que quiero' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Proponiendo…' })).toBeDisabled()
  })

  it('envía con Intro y conserva Mayús + Intro para escribir otra línea', () => {
    const onSubmit = vi.fn()

    render(
      <OutfitComposer
        description="camisa blanca y pantalón negro"
        isLoading={false}
        mode="describe"
        onChange={vi.fn()}
        onModeChange={vi.fn()}
        onSubmit={onSubmit}
      />,
    )

    const textarea = screen.getByRole('textbox', { name: 'Describe tu outfit' })
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true })
    expect(onSubmit).not.toHaveBeenCalled()

    fireEvent.keyDown(textarea, { key: 'Enter' })
    expect(onSubmit).toHaveBeenCalledOnce()
  })

  it('no permite revisar una descripción vacía', () => {
    render(
      <OutfitComposer
        description="   "
        isLoading={false}
        mode="describe"
        onChange={vi.fn()}
        onModeChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    )

    expect(screen.getByRole('button', { name: 'Revisar outfit' })).toBeDisabled()
    expect(
      screen.getByRole('button', { name: 'Iniciar dictado por voz' }),
    ).toBeDisabled()
    expect(screen.getByRole('status')).toHaveTextContent(
      'El dictado por voz no está disponible en este navegador.',
    )
  })

  it('añade el dictado al texto sin enviarlo y permite detener la escucha', () => {
    installSpeechRecognition()
    const onSubmit = vi.fn()

    function DictationHarness() {
      const [description, setDescription] = useState('camisa blanca')
      return (
        <OutfitComposer
          description={description}
          isLoading={false}
          mode="describe"
          onChange={setDescription}
          onModeChange={vi.fn()}
          onSubmit={onSubmit}
        />
      )
    }

    render(<DictationHarness />)
    fireEvent.click(
      screen.getByRole('button', { name: 'Iniciar dictado por voz' }),
    )

    const speech = FakeSpeechRecognition.latest
    expect(speech).not.toBeNull()
    expect(speech?.start).toHaveBeenCalledOnce()
    expect(speech?.lang).toBe('es-ES')
    expect(speech?.continuous).toBe(true)
    expect(speech?.interimResults).toBe(false)
    expect(
      screen.getByRole('button', { name: 'Detener dictado por voz' }),
    ).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()

    act(() => {
      speech?.emitFinal('y pantalón negro de pierna ancha')
    })

    const textarea = screen.getByRole('textbox', { name: 'Describe tu outfit' })
    expect(textarea).toHaveValue(
      'camisa blanca y pantalón negro de pierna ancha',
    )
    expect(onSubmit).not.toHaveBeenCalled()

    fireEvent.click(
      screen.getByRole('button', { name: 'Detener dictado por voz' }),
    )

    expect(speech?.stop).toHaveBeenCalledOnce()
    expect(
      screen.getByRole('button', { name: 'Iniciar dictado por voz' }),
    ).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(textarea).toHaveFocus()
  })

  it('restaura el botón sin mostrar detalles cuando falla el dictado', () => {
    installSpeechRecognition()

    render(
      <OutfitComposer
        description="camisa blanca"
        isLoading={false}
        mode="describe"
        onChange={vi.fn()}
        onModeChange={vi.fn()}
        onSubmit={vi.fn()}
      />,
    )

    fireEvent.click(
      screen.getByRole('button', { name: 'Iniciar dictado por voz' }),
    )
    act(() => {
      FakeSpeechRecognition.latest?.emitError('not-allowed')
    })

    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    expect(
      screen.getByRole('button', { name: 'Iniciar dictado por voz' }),
    ).toBeEnabled()
  })
})
