// Export and sharing helpers: calendar (.ics), Google Maps routes and the system share sheet.
// Everything is built on the device; nothing is sent anywhere until the traveller chooses to share.

const MAPS_MODES = { walk: 'walking', bicycle: 'bicycling', transit: 'transit', car: 'driving', motorbike: 'driving', auto: 'driving', taxi: 'driving' }

export const mapsPlaceUrl = (place) => place?.lat != null && place?.lon != null
  ? `https://www.google.com/maps/search/?api=1&query=${place.lat},${place.lon}`
  : null

// Google Maps directions through every stop (Maps allows 9 waypoints between origin and destination).
export const mapsRouteUrl = (stops, { origin, mode } = {}) => {
  const points = stops.filter((s) => s.lat != null && s.lon != null).map((s) => `${s.lat},${s.lon}`)
  if (!points.length) return null
  const params = new URLSearchParams({ api: '1', destination: points[points.length - 1] })
  if (origin) params.set('origin', `${origin.lat},${origin.lon}`)
  else if (points.length > 1) params.set('origin', points[0])
  const via = (origin ? points.slice(0, -1) : points.slice(1, -1)).slice(0, 9)
  if (via.length) params.set('waypoints', via.join('|'))
  params.set('travelmode', MAPS_MODES[mode] || 'walking')
  return `https://www.google.com/maps/dir/?${params.toString()}`
}

const icsText = (value) => String(value || '').replace(/\\/g, '\\\\').replace(/\n/g, '\\n').replace(/([,;])/g, '\\$1')
const icsStamp = (date, time) => `${date.replaceAll('-', '')}T${time.replace(':', '')}00`
const fold = (line) => line.length <= 74 ? line : line.match(/.{1,74}/g).join('\r\n ')

// One calendar event per stop, in the destination's timezone.
export const planToIcs = (plan, cityName) => {
  const date = (plan.start || '').slice(0, 10)
  if (!date || !plan.stops?.length) return null
  const tz = plan.timezone
  const when = (time) => (tz ? `;TZID=${tz}:` : ':') + icsStamp(date, time)
  const now = new Date().toISOString().replace(/[-:]/g, '').replace(/\.\d+Z$/, 'Z')
  const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//GeoGuide//Plan//EN', 'CALSCALE:GREGORIAN']
  plan.stops.forEach((stop, index) => {
    lines.push('BEGIN:VEVENT', `UID:${plan.id || date}-${index}@geoguide`, `DTSTAMP:${now}`, `DTSTART${when(stop.arrive)}`, `DTEND${when(stop.depart)}`,
      `SUMMARY:${icsText(stop.name)}`, `LOCATION:${icsText(stop.address || (stop.lat != null ? `${stop.lat},${stop.lon}` : cityName))}`,
      `DESCRIPTION:${icsText([`Stop ${index + 1} of ${plan.stops.length}${cityName ? ` in ${cityName}` : ''}.`, stop.leg ? `Getting there: ${stop.leg.minutes} min by ${String(stop.leg.mode).replace('_', ' ')} (estimate).` : '', mapsPlaceUrl(stop) || ''].filter(Boolean).join('\n'))}`,
      ...(stop.lat != null ? [`GEO:${stop.lat};${stop.lon}`] : []), 'END:VEVENT')
  })
  lines.push('END:VCALENDAR')
  return lines.map(fold).join('\r\n')
}

export const downloadFile = (name, text, type = 'text/calendar') => {
  const url = URL.createObjectURL(new Blob([text], { type }))
  const link = Object.assign(document.createElement('a'), { href: url, download: name })
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

// The system share sheet when available, otherwise the clipboard. Returns 'shared' | 'copied' | 'failed'.
export const shareText = async ({ title, text, url }) => {
  try {
    if (navigator.share) {
      await navigator.share({ title, text, url })
      return 'shared'
    }
    await navigator.clipboard.writeText([text, url].filter(Boolean).join('\n'))
    return 'copied'
  } catch (error) {
    return error?.name === 'AbortError' ? 'cancelled' : 'failed'
  }
}

export const planSummary = (plan, cityName) => [
  `My ${cityName ? `${cityName} ` : ''}plan${plan.start ? ` for ${plan.start.slice(0, 10)}` : ''}:`,
  ...plan.stops.map((stop, index) => `${index + 1}. ${stop.arrive}–${stop.depart} ${stop.name}`),
].join('\n')
