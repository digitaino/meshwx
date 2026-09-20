// The one file in src/meshwx that touches the filesystem (PORTING §1, §8).
//
// It is deliberately NOT re-exported from index.js: the browser has no `node:fs`, and a bundler
// that followed the index would choke on it. Tests import it by path:
//
//     import { nodeBundleLoader } from '../src/meshwx/nodeLoader.js';
//     await MeshWXTables.load(nodeBundleLoader());
//
// The browser's loader is the same shape and fetches `./data/<fileName>` instead.

import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';

/** `meshcore_weather/client_data/` in the bot repository, beside this web client. */
export const defaultBundleDirectory = new URL(
  '../../../meshcore_weather/client_data/',
  import.meta.url,
);

/**
 * A PORTING §8 loader over the repository's preload bundle:
 * `async (fileName) => parsedJSON`, e.g. `loader('index.json')`.
 *
 * `directory` overrides where it reads from (a URL or a path); `MESHWX_CLIENT_DATA` in the
 * environment does the same, for a checkout laid out differently.
 */
export function nodeBundleLoader({ directory } = {}) {
  const base = directory != null
    ? asDirectoryURL(directory)
    : (process.env.MESHWX_CLIENT_DATA != null
      ? asDirectoryURL(process.env.MESHWX_CLIENT_DATA)
      : defaultBundleDirectory);
  return async (fileName) => {
    const file = new URL(fileName, base);
    return JSON.parse(await readFile(fileURLToPath(file), 'utf8'));
  };
}

function asDirectoryURL(value) {
  if (value instanceof URL) return value.href.endsWith('/') ? value : new URL(`${value.href}/`);
  const text = String(value);
  if (text.startsWith('file://')) {
    return new URL(text.endsWith('/') ? text : `${text}/`);
  }
  return new URL(`file://${text.endsWith('/') ? text : `${text}/`}`);
}
