import { ChevronDown } from 'lucide-react'
import { useId, type ReactNode } from 'react'

export function UnreadDot() {
  return <span className="room-play__unread-dot" role="img" aria-label="有新增内容" />
}

export function InformationDisclosure({
  title, expanded, onToggle, unread = false, children, entry = false,
}: {
  title: string
  expanded: boolean
  onToggle: () => void
  unread?: boolean
  children: ReactNode
  entry?: boolean
}) {
  const contentId = useId()
  return (
    <section className={`room-play__information-${entry ? 'entry' : 'section'}`}>
      <button
        type="button"
        className="room-play__information-summary"
        aria-label={title}
        aria-description={unread ? '有新增内容' : undefined}
        aria-expanded={expanded}
        aria-controls={contentId}
        onClick={onToggle}
      >
        <span className="room-play__information-name"><span className="room-play__information-text">{title}</span>{unread && <UnreadDot />}</span>
        <ChevronDown aria-hidden="true" className={expanded ? 'is-expanded' : ''} />
      </button>
      <div id={contentId} hidden={!expanded} className="room-play__information-content">
        {children}
      </div>
    </section>
  )
}
