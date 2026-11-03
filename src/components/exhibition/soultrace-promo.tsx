import { ExternalLink } from 'lucide-react'

type SoulTracePromoProps = {
  title: string
  body: string
  cta: string
  className?: string
  minimal?: boolean
  onCtaClick?: () => void
}

/** The single existing Soul Trace promotional card, reused wherever it appears. */
export function SoulTracePromo({ title, body, cta, className = '', minimal = false, onCtaClick }: SoulTracePromoProps) {
  return (
    <section className={`passport-final-soultrace ${className}`.trim()}>
      <div>
        {!minimal && <h2>{title}</h2>}
        {!minimal && <p>{body}</p>}
        <a href="https://soultrace.pet" target="_blank" rel="noopener noreferrer" onClick={onCtaClick}>
          {cta} <ExternalLink size={15} />
        </a>
      </div>
    </section>
  )
}
