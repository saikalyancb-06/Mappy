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

// ---- calendar dates (ISO yyyy-mm-dd strings; parsed at noon so time zones never shift the day) ----
const atNoon = (iso) => new Date(`${iso}T12:00:00`)
const toIso = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`

export const formatDay = (iso, withYear = true) => {
  if (!iso) return null
  const date = atNoon(iso.slice(0, 10))
  if (Number.isNaN(date.getTime())) return null
  return date.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', ...(withYear ? { year: 'numeric' } : {}) })
}

export const addDays = (iso, days) => {
  const date = atNoon(iso)
  date.setDate(date.getDate() + days)
  return toIso(date)
}

export const upcomingSaturday = (iso) => {
  const date = atNoon(iso)
  const day = date.getDay() // 0 Sunday … 6 Saturday
  return day === 6 || day === 0 ? iso : addDays(iso, 6 - day)
}

export const relativeDay = (days) => {
  if (days == null) return null
  if (days === 0) return 'Today'
  if (days === 1) return 'Tomorrow'
  if (days === -1) return 'Yesterday'
  return days > 0 ? `In ${days} days` : `${-days} days ago`
}
