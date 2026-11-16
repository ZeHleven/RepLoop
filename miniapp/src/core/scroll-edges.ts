// Physical pixel geometry from the native viewport, not estimated record counts.
export function scrollEdgeSizes (top: number, content: number, viewport: number, width: number) {
  if (![top, content, viewport, width].every(Number.isFinite) || viewport <= 0 || width <= 0) return { top: 0, bottom: 0 }
  const max = Math.max(0, content - viewport)
  const position = Math.max(0, Math.min(max, top))
  const limit = Math.min(width * 112 / 750, viewport / 2)
  const edge = (distance: number) => distance <= 1 ? 0 : Math.round(Math.min(limit, distance))
  return { top: edge(position), bottom: edge(max - position) }
}
