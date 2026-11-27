import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import IoC from '@/modules/ioc'
import { SERVICES, type IHomeService } from '@/types'

const toolLinks = [
  {
    to: '/translation',
    label: 'Translation',
    hint: 'translate your ISO in the browser'
  },
  { to: '/glyphs', label: 'Glyphs', hint: 'glyph textures for PCSX2' },
  { to: '/cheats', label: 'Cheats', hint: '22 ISO cheats' },
  { to: '/voices', label: 'Voices', hint: '85 voice banks' }
]

function Home() {
  const homeService = IoC.getOrCreateInstance<IHomeService>(SERVICES.HOME)

  const [current, setCurrent] = useState(0)
  const [isLoading, setIsLoading] = useState(true)
  const [loadedImages, setLoadedImages] = useState<Record<string, boolean>>({})
  const [dragOffset, setDragOffset] = useState(0)
  const [isDragging, setIsDragging] = useState(false)
  const [imageWidth, setImageWidth] = useState(0)

  const dragStart = useRef(0)

  const images = homeService.getImages()
  const image = images[current]

  const backIndex =
    dragOffset < 0
      ? (current + 1) % images.length
      : (current - 1 + images.length) % images.length
  const backImage = images[backIndex]

  function markLoaded(src: string) {
    setLoadedImages((loaded) =>
      loaded[src] ? loaded : { ...loaded, [src]: true }
    )
  }

  function prev() {
    const target = images[(current - 1 + images.length) % images.length]

    if (!loadedImages[target.src]) setIsLoading(true)
    setCurrent((current) => (current - 1 + images.length) % images.length)
  }

  function next() {
    const target = images[(current + 1) % images.length]

    if (!loadedImages[target.src]) setIsLoading(true)
    setCurrent((current) => (current + 1) % images.length)
  }

  function goTo(index: number) {
    if (!loadedImages[images[index].src]) setIsLoading(true)
    setCurrent(index)
  }

  function handlePointerDown(event: React.PointerEvent<HTMLDivElement>) {
    event.preventDefault()

    dragStart.current = event.clientX
    setImageWidth(event.currentTarget.clientWidth)

    setIsDragging(true)

    event.currentTarget.setPointerCapture(event.pointerId)
  }

  function handlePointerMove(event: React.PointerEvent<HTMLDivElement>) {
    if (!isDragging) return

    setDragOffset(event.clientX - dragStart.current)
  }

  function handlePointerUp(event: React.PointerEvent<HTMLDivElement>) {
    if (!isDragging) return

    const offset = event.clientX - dragStart.current
    const threshold = imageWidth / 4

    if (offset < -threshold) {
      next()
      setDragOffset(imageWidth + offset)
      requestAnimationFrame(() => {
        setIsDragging(false)
        setDragOffset(0)
      })
    } else if (offset > threshold) {
      prev()
      setDragOffset(offset - imageWidth)
      requestAnimationFrame(() => {
        setIsDragging(false)
        setDragOffset(0)
      })
    } else {
      setIsDragging(false)
      setDragOffset(0)
    }
  }

  function handlePointerCancel() {
    setIsDragging(false)
    setDragOffset(0)
  }

  return (
    <>
      <section id="center">
        <h1>Valkyrie Profile 2 - Silmeria Translation</h1>
        <p>
          Fan Translation Project.{' '}
          <a
            href={homeService.getProjectURL()}
            target="_blank"
            rel="noreferrer"
            className="link"
          >
            GitHub
          </a>
        </p>
      </section>

      <section className="carousel" aria-roledescription="carousel">
        <div
          className="carousel-viewport"
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerCancel}
        >
          {isLoading && (
            <div
              className="spinner carousel-spinner"
              role="status"
              aria-label="Loading image"
            ></div>
          )}
          {dragOffset !== 0 && (
            <img
              className="carousel-image carousel-image-back"
              src={`${homeService.getImageBase()}/${backImage.src}`}
              alt={backImage.alt}
              draggable={false}
              loading="lazy"
              decoding="async"
              onLoad={() => markLoaded(backImage.src)}
              style={{
                transform: `translateX(${
                  dragOffset < 0
                    ? imageWidth + dragOffset
                    : dragOffset - imageWidth
                }px)`,
                transition: isDragging ? 'none' : 'transform 0.25s ease'
              }}
            />
          )}
          <img
            className="carousel-image"
            src={`${homeService.getImageBase()}/${image.src}`}
            alt={image.alt}
            draggable={false}
            loading="lazy"
            decoding="async"
            onLoad={() => {
              markLoaded(image.src)
              setIsLoading(false)
            }}
            style={{
              opacity: isLoading ? 0 : 1,
              transform: `translateX(${dragOffset}px)`,
              transition: isDragging
                ? 'none'
                : 'transform 0.25s ease, opacity 0.2s ease'
            }}
          />
        </div>
        <div className="carousel-caption">{image.alt}</div>
        <div className="row">
          <button className="btn" onClick={prev} aria-label="Previous image">
            ‹
          </button>
          <button className="btn" onClick={next} aria-label="Next image">
            ›
          </button>
        </div>
        <div className="carousel-dots">
          {images.map((item, index) => (
            <button
              key={item.src}
              type="button"
              className={`dot ${index === current ? 'active' : ''}`}
              onClick={() => goTo(index)}
              aria-label={`Go to ${item.alt}`}
            />
          ))}
        </div>
      </section>

      <section className="video-section">
        <h2>Dub</h2>
        <p>
          text-to-speech generated dub for the first cutscene, as a proof of
          concept
        </p>
        <video
          className="video"
          controls
          preload="metadata"
          src={homeService.getDubVideo()}
        />
      </section>

      <section className="tool-grid">
        <h2>Tools</h2>
        <div className="tool-cards">
          {toolLinks.map((tool) => (
            <Link key={tool.to} to={tool.to} className="tool-card">
              <span className="tool-card-label">{tool.label}</span>
              <span className="tool-card-hint">{tool.hint}</span>
            </Link>
          ))}
        </div>
      </section>
    </>
  )
}

export default Home
