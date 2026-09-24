// Formatting helpers. Missing values stay missing: callers render nothing rather than a guess.

const CURRENCY = { INR: '₹', USD: '$', EUR: '€', GBP: '£' }

export const formatDistance = (km) => {
  if (km == null || Number.isNaN(Number(km))) return null
  const value = Number(km)
  return value < 1 ? `${Math.round(value * 100) * 10} m` : `${value.toFixed(value < 10 ? 1 : 0)} km`
}

export const formatMoney = (amount, currency) => {
  // Exact decimal text in, formatted text out: money never goes through float arithmetic.
  if (amount == null || amount === '') return null
  const text = String(amount).trim()
  if (!/^-?\d+(\.\d+)?$/.test(text)) return null
  const [whole, fraction = ''] = text.split('.')
  if (/^-?0+$/.test(whole) && /^0*$/.test(fraction)) return 'Free'
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ',')
  const cents = fraction.padEnd(2, '0').slice(0, 2)
  return `${CURRENCY[currency] || (currency ? `${currency} ` : '')}${grouped}${cents === '00' ? '' : `.${cents}`}`
}

export const formatMinutes = (minutes) => {
  if (minutes == null) return null
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return hours ? `${hours} h${rest ? ` ${rest} min` : ''}` : `${rest} min`
}

export const openLabel = (place) => {
  if (!place || place.open_status === 'unknown' || !place.open_status) return null
  const detail = place.open_detail || {}
  if (place.open_status === 'open') return detail.always_open ? 'Open' : `Open${detail.closes_at ? ` · closes ${detail.closes_at}` : ''}`
  return `Closed${detail.opens_at ? ` · opens ${detail.opens_at}` : ''}`
}

export const titleCase = (text) => (text || '').replaceAll('_', ' ').replace(/\b\w/g, (letter) => letter.toUpperCase())

export const localTimeLabel = (iso) => {
  if (!iso) return null
  // Keep the destination's own wall-clock time (the ISO string carries its offset).
  const match = /T(\d{2}):(\d{2})/.exec(iso)
  const date = new Date(`${iso.slice(0, 10)}T12:00:00`)
  const weekday = Number.isNaN(date.getTime()) ? '' : date.toLocaleDateString([], { weekday: 'long' })
  return match ? `${weekday} ${match[1]}:${match[2]}`.trim() : null
}

export const mapsLink = (place) => (place?.lat != null && place?.lon != null ? `https://www.google.com/maps/dir/?api=1&destination=${place.lat},${place.lon}` : null)
