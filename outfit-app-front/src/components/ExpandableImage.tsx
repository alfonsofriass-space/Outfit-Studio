import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

interface ExpandableImageProps {
  src: string
  alt: string
  className: string
  label: string
}

export function ExpandableImage({
  src,
  alt,
  className,
  label,
}: ExpandableImageProps) {
  const [isOpen, setIsOpen] = useState(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const closeRef = useRef<HTMLButtonElement>(null)
  const titleId = useId()

  useEffect(() => {
    if (!isOpen) return

    const previousOverflow = document.body.style.overflow
    const trigger = triggerRef.current
    document.body.style.overflow = 'hidden'
    closeRef.current?.focus()

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsOpen(false)
      }
      if (event.key === 'Tab') {
        event.preventDefault()
        closeRef.current?.focus()
      }
    }

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
      document.body.style.overflow = previousOverflow
      trigger?.focus()
    }
  }, [isOpen])

  return (
    <>
      <div className="expandable-image">
        <img className={className} src={src} alt={alt} />
        <button
          ref={triggerRef}
          className="expandable-image__trigger"
          type="button"
          aria-label={`Ampliar ${label.toLowerCase()}`}
          onClick={() => setIsOpen(true)}
        >
          <svg aria-hidden="true" viewBox="0 0 20 20">
            <path d="M7.5 3.5h-4v4M12.5 3.5h4v4M7.5 16.5h-4v-4M12.5 16.5h4v-4" />
          </svg>
        </button>
      </div>

      {isOpen &&
        createPortal(
          <div
            className="image-preview"
            role="dialog"
            aria-modal="true"
            aria-labelledby={titleId}
            onMouseDown={(event) => {
              if (event.target === event.currentTarget) setIsOpen(false)
            }}
          >
            <div className="image-preview__panel">
              <div className="image-preview__header">
                <span id={titleId}>{label}</span>
                <button
                  ref={closeRef}
                  className="image-preview__close"
                  type="button"
                  aria-label="Cerrar vista ampliada"
                  onClick={() => setIsOpen(false)}
                >
                  <svg aria-hidden="true" viewBox="0 0 20 20">
                    <path d="m5 5 10 10M15 5 5 15" />
                  </svg>
                </button>
              </div>
              <div className="image-preview__stage">
                <img src={src} alt={alt} />
              </div>
            </div>
          </div>,
          document.body,
        )}
    </>
  )
}
