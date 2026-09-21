// Reading a JSON file that ships with the client.
//
// Normally that is a fetch, relative to the page. A single-file build (`tools/single-file.mjs`)
// has nothing to fetch: a page opened straight from a folder cannot read a file beside it at all,
// so that build carries each one in the page instead, as
// `<script type="application/json" id="meshwx:data/places.json">`. Everything that reads a shipped
// file goes through here, so the same code runs from a folder, from a server and from one file.

/** The file's contents if this page carries it, or null. Never throws. */
export function embeddedJSON(path) {
  const node = globalThis.document?.getElementById(`meshwx:${path}`)
  if (!node) return null
  try {
    return JSON.parse(node.textContent)
  } catch {
    return null
  }
}

/** The file, from the page if it is in it, otherwise over the network. */
export async function readJSON(path) {
  const embedded = embeddedJSON(path)
  if (embedded !== null) return embedded
  const response = await fetch(path)
  if (!response.ok) throw new Error(`${path}: ${response.status}`)
  return response.json()
}
