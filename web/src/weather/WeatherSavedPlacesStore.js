// Port of MC1Services/Services/Weather/WeatherSavedPlacesStore.swift (docs/PORTING.md).

/**
 * The places the user keeps in Places, on this phone (docs/MESHWX_UI.md §5), and which of them
 * have their bell on (§16).
 *
 * Device-local like the bot choice: which towns someone looks at is a fact about this phone, not
 * about a radio, and not worth a backup row. Stored as JSON under one key, so a field added to
 * `WeatherSavedPlace` needs no migration — an unreadable value reads as an empty list and the
 * next pick writes a good one.
 *
 * It lives in the service layer, not beside the screen, because the alert evaluator
 * (`WeatherAlertNotifier`) runs at service lifetime and reads the very same list: a watched place
 * is a saved place with its bell on, and there is one of it.
 *
 * Two deviations from the Swift:
 *
 * - `defaults` is an injected **synchronous** key-value store, `{ get(key), set(key, value) }`.
 *   `UserDefaults` is synchronous and so is everything that reads this list — the evaluator asks
 *   for the watch list in the middle of applying a message — so the browser app backs this with a
 *   memory cache it hydrates from IndexedDB at boot, rather than making the read async.
 * - The list rules (`WeatherSavedPlaces`) live under `Screen/`, so they are in `src/screen/` and
 *   this layer may not import them (PORTING.md §9). They are injected as `places`, an object with
 *   `{ limit, ordered(list), apply(edit, { to }), dropped(updated, { from }) }` — the screen
 *   layer's `WeatherSavedPlaces` namespace, handed over at construction.
 */
export class WeatherSavedPlacesStore {
  static key = 'weather.savedPlaces'

  /**
   * @param defaults the synchronous key-value store.
   * @param places the screen layer's `WeatherSavedPlaces` rules.
   */
  constructor({ defaults, places }) {
    this.defaults = defaults
    this.rules = places
  }

  get places() {
    const stored = this.defaults.get(WeatherSavedPlacesStore.key)
    if (!Array.isArray(stored)) return []
    return this.rules.ordered(stored)
  }

  set places(newValue) {
    this.defaults.set(WeatherSavedPlacesStore.key, this.rules.ordered(newValue))
  }

  /** The places whose bell is on, newest choice first. */
  get watched() {
    return this.places.filter((place) => place.isWatched)
  }

  /**
   * Applies one edit to the list **as this store holds it**, and returns what it now holds.
   *
   * This is the only way the list is written. The rule it enforces is that nothing may persist a
   * list it did not first load: a screen's copy can be a second old, or empty because the model
   * was still starting, and writing that copy back silently deletes everything saved since. On a
   * real phone it deleted four saved places and left the one that had just been picked
   * (docs/MESHWX_UI.md §3.1 U-1).
   *
   * A write that would drop a place nobody asked to drop is refused outright, and the stored
   * list is returned unchanged. Only a `remove` edit may take a row out, and only the ceiling may
   * take one off the end of a list that just grew.
   */
  apply(edit) {
    const loaded = this.places
    const updated = this.rules.apply(edit, { to: loaded })
    const dropped = new Set(this.rules.dropped(updated, { from: loaded }))
    const removed = removedID(edit)
    if (removed != null) dropped.delete(removed)
    // Adding a thirteenth place pushes the oldest unwatched row off the end, which the ceiling
    // asked for (`WeatherSavedPlaces.ordered`) — nothing else is allowed to shorten the list.
    const ceiling = adds(edit) && updated.length >= this.rules.limit
    if (dropped.size > 0 && !ceiling) return loaded
    this.places = updated
    return updated
  }
}

/** Whether the edit can make the list longer, and so let the ceiling push a row off the end. */
function adds(edit) {
  return edit.kind === 'remember'
}

/** The one id this edit asks to be dropped. */
function removedID(edit) {
  return edit.kind === 'remove' ? edit.id : null
}
