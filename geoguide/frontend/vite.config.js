import { createHash } from 'node:crypto'
import { readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Writes the build's hashed JS/CSS files into dist/sw.js so the service worker can precache the whole
// app on install (offline works even for screens never opened online). The list also changes the
// worker's bytes on every release, which is what makes browsers pick up a new version.
function precacheManifest() {
  let files = []
  return {
    name: 'geoguide-precache-manifest',
    apply: 'build',
    generateBundle(_, bundle) {
      files = Object.keys(bundle).filter((name) => /\.(js|css)$/.test(name)).map((name) => `/${name}`).sort()
    },
    closeBundle() {
      const path = resolve('dist', 'sw.js')
      const version = createHash('sha1').update(files.join('|')).digest('hex').slice(0, 10)
      const source = readFileSync(path, 'utf8')
        .replace("const PRECACHE = []", `const PRECACHE = ${JSON.stringify(files)}`)
        .replace("const BUILD = 'dev'", `const BUILD = '${version}'`)
      writeFileSync(path, source)
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), precacheManifest()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
