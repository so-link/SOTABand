import { useEffect, useRef } from 'react'

export interface AutocompleteItem {
  name: string
  id: string
}

interface AutocompleteDropdownProps {
  visible: boolean
  items: AutocompleteItem[]
  selectedIndex: number
  position: { top: number; left: number }
  loading?: boolean
  formatResult: (name: string) => string
  onSelect: (item: AutocompleteItem) => void
  onClose: () => void
}

export function AutocompleteDropdown({
  visible,
  items,
  selectedIndex,
  position,
  loading = false,
  formatResult,
  onSelect,
  onClose,
}: AutocompleteDropdownProps) {
  const listRef = useRef<HTMLDivElement>(null)

  // Scroll selected item into view
  useEffect(() => {
    if (!listRef.current || !visible) return
    const selected = listRef.current.children[selectedIndex] as HTMLElement | undefined
    if (selected) {
      selected.scrollIntoView({ block: 'nearest' })
    }
  }, [selectedIndex, visible])

  // Close on outside click
  useEffect(() => {
    if (!visible) return
    const handleClick = (e: MouseEvent) => {
      if (listRef.current && !listRef.current.contains(e.target as Node)) {
        onClose()
      }
    }
    // Delay to avoid catching the click that opened the dropdown
    const timer = setTimeout(() => document.addEventListener('mousedown', handleClick), 0)
    return () => {
      clearTimeout(timer)
      document.removeEventListener('mousedown', handleClick)
    }
  }, [visible, onClose])

  if (!visible) return null
  if (!loading && items.length === 0) return null

  return (
    <div
      ref={listRef}
      className="fixed z-50 w-72 max-h-48 overflow-y-auto rounded-lg border border-maia-border bg-white shadow-lg py-1"
      style={{ top: position.top, left: position.left }}
    >
      {loading && items.length === 0 ? (
        <div className="px-3 py-2 text-[12px] text-maia-text-muted">加载中...</div>
      ) : (
        items.map((item, i) => (
          <button
            key={item.id}
            className={`w-full text-left px-3 py-1.5 flex flex-col gap-0 transition-colors ${
              i === selectedIndex
                ? 'bg-maia-accent/10 text-maia-accent'
                : 'hover:bg-maia-bg text-maia-text'
            }`}
            onMouseDown={(e) => {
              e.preventDefault() // Prevent blur on textarea before selection
              onSelect(item)
            }}
          >
            <span className="text-[12px] font-medium tracking-wide truncate">
              {formatResult(item.name)}
            </span>
            <span className="text-[10px] text-maia-text-muted truncate">{item.id}</span>
          </button>
        ))
      )}
    </div>
  )
}
