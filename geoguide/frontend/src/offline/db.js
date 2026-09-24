// Minimal IndexedDB key-value store for offline city packs (no dependency).
const DB_NAME = 'geoguide-offline'
const STORE = 'packs'
let opening = null

const open = () => {
  if (opening) return opening
  opening = new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') { reject(new Error('IndexedDB is not available')); return }
    const request = indexedDB.open(DB_NAME, 1)
    request.onupgradeneeded = () => request.result.createObjectStore(STORE)
    request.onsuccess = () => resolve(request.result)
    request.onerror = () => reject(request.error)
  })
  opening.catch(() => { opening = null })
  return opening
}

const run = async (mode, fn) => {
  const db = await open()
  return new Promise((resolve, reject) => {
    const tx = db.transaction(STORE, mode)
    const request = fn(tx.objectStore(STORE))
    tx.oncomplete = () => resolve(request?.result)
    tx.onerror = () => reject(tx.error)
    tx.onabort = () => reject(tx.error || new Error('aborted'))
  })
}

export const idbGet = (key) => run('readonly', (store) => store.get(key))
export const idbSet = (key, value) => run('readwrite', (store) => store.put(value, key))
export const idbDelete = (key) => run('readwrite', (store) => store.delete(key))
export const idbKeys = () => run('readonly', (store) => store.getAllKeys())
