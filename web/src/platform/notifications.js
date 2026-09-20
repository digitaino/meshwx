// Alert notifications in a browser: the poster `WeatherAlertNotifier` posts through
// (docs/PORTING.md §7 — the rules are ported, the iOS mechanism is not).
//
// **Be honest about what this is.** A browser tab is not a phone's notification centre. A page
// notification exists while the page exists: close the tab and nothing more arrives, because the
// radio link, the weather service and the evaluator all live in the page. A service worker can
// outlive the tab, but it has no Bluetooth or serial connection to the radio, so it has nothing
// to evaluate. Every string this module's screens show says so (`web.notifications.*`).
//
// The poster interface, exactly as `src/weather/WeatherAlertNotifier.js` calls it:
//
//     post(notification)        -> Promise<void>
//     remove({ identifiers })   -> Promise<void>
//     deliveredIdentifiers()    -> Promise<string[]>
//     isAuthorized()            -> Promise<boolean>
//
// plus what the tool's model reads: `authorization` and `request()`.
//
// Nothing here touches a browser API at module level: every reference is inside a method or a
// default argument, which is evaluated when the object is constructed.

/** The authorization values the app speaks, shared with `LocationService`. */
export const NotificationAuthorization = Object.freeze({
  notDetermined: 'notDetermined',
  authorized: 'authorized',
  denied: 'denied',
  unsupported: 'unsupported',
})

/**
 * How a tapped notification reaches the app. The app passes
 * `WeatherAlertNotificationRouting.receive`; this layer may not import it, because `platform` is
 * below `app` (docs/PORTING.md §9).
 *
 * @typedef {(tap: { userInfo: Record<string, string>, at: number }) => void} NotificationTapHandler
 */

/**
 * The poster the app boots with. `src/app/main.js` imports this name and hands the result to
 * `RadioConnection`, which is what `WeatherHost.notifications` is.
 *
 * A tap has nowhere to go until something wires one: `WeatherAlertNotificationRouting.install`
 * sets `onTap`, and the tool's model does that on `start()`. Until then a notification still
 * shows and a tap simply focuses the page.
 */
export function createNotifications(options = {}) {
  const poster = new WebNotifications(options)
  poster.start()
  return poster
}

export class WebNotifications {
  /**
   * @param {object} options
   * @param {NotificationTapHandler} [options.onTap]
   * @param {any} [options.notification] the `Notification` constructor, for a test.
   * @param {any} [options.serviceWorker] `navigator.serviceWorker`, for a test.
   * @param {() => number} [options.now]
   * @param {() => void} [options.focus] brings the page forward after a tap.
   */
  constructor({
    onTap = null,
    notification = globalThis.Notification,
    serviceWorker = globalThis.navigator?.serviceWorker,
    now = () => Date.now(),
    focus = () => globalThis.focus?.(),
  } = {}) {
    this.Notification = notification ?? null
    this.serviceWorker = serviceWorker ?? null
    this.onTap = onTap
    this.now = now
    this.focus = focus
    /** Live page notifications by identifier, so `remove` and `deliveredIdentifiers` can answer. */
    this.live = new Map()
    this.registration = null
    this.registrationLookup = null
    this.messageListener = null
  }

  // MARK: - Permission

  /** 'notDetermined' | 'authorized' | 'denied' | 'unsupported'. */
  get authorization() {
    const permission = this.Notification?.permission
    if (permission == null) return NotificationAuthorization.unsupported
    if (permission === 'granted') return NotificationAuthorization.authorized
    if (permission === 'denied') return NotificationAuthorization.denied
    return NotificationAuthorization.notDetermined
  }

  /**
   * Asks the browser for permission. **From a tap only** — Safari and Chrome both refuse a
   * request that did not come from a gesture, and the tool asks exactly once, when the user turns
   * on their first bell (docs/MESHWX_UI.md §16).
   *
   * Resolves true when notifications may now be posted.
   */
  async request() {
    if (this.Notification == null) return false
    if (this.authorization === NotificationAuthorization.authorized) return true
    try {
      const answer = await this.Notification.requestPermission()
      return answer === 'granted'
    } catch {
      return false
    }
  }

  async isAuthorized() {
    return this.authorization === NotificationAuthorization.authorized
  }

  // MARK: - Lifecycle

  /**
   * Starts listening for taps on notifications a service worker showed.
   *
   * A notification the page itself created carries its own `onclick` and needs none of this. One
   * a service worker showed is clicked outside the page, so the worker has to post the click
   * back; this listens for `{ type: 'meshwx.notificationclick', userInfo }` on
   * `navigator.serviceWorker`. A worker that does not post it simply never routes a tap, and the
   * notification still shows.
   */
  start() {
    if (this.serviceWorker == null || this.messageListener != null) return
    this.messageListener = (event) => {
      const data = event?.data
      if (data?.type !== 'meshwx.notificationclick') return
      this.handleTap(data.userInfo)
    }
    this.serviceWorker.addEventListener?.('message', this.messageListener)
  }

  stop() {
    if (this.messageListener != null) {
      this.serviceWorker?.removeEventListener?.('message', this.messageListener)
      this.messageListener = null
    }
    for (const one of this.live.values()) one.close?.()
    this.live.clear()
  }

  // MARK: - Posting

  /**
   * Shows one `WeatherAlertNotification`.
   *
   * `identifier` becomes the tag, which is what makes a repeat of a warning replace the one on
   * screen rather than stack beside it — the same thing the iOS identifier does. `sound` false is
   * a silent delivery (a replacement, or the rank-6 toggle), which the web spells `silent: true`.
   */
  async post(notification) {
    if (!(await this.isAuthorized())) return
    const content = notification.content ?? {}
    const body = content.subtitle == null ? content.body : `${content.subtitle}\n${content.body}`
    const options = {
      body,
      tag: notification.identifier,
      silent: notification.sound !== true,
      renotify: notification.sound === true,
      requireInteraction: false,
      data: notificationUserInfo(notification),
    }
    const registration = await this.workerRegistration()
    if (registration != null) {
      try {
        await registration.showNotification(content.title ?? '', options)
        return
      } catch {
        /* Fall through to the page notification. */
      }
    }
    if (this.Notification == null) return
    try {
      const posted = new this.Notification(content.title ?? '', options)
      posted.onclick = () => {
        this.focus?.()
        posted.close?.()
        this.handleTap(options.data)
      }
      posted.onclose = () => {
        if (this.live.get(notification.identifier) === posted) this.live.delete(notification.identifier)
      }
      this.live.get(notification.identifier)?.close?.()
      this.live.set(notification.identifier, posted)
    } catch {
      // Android Chrome refuses `new Notification()` outright: with no service worker willing to
      // show it there is nothing this page can do, and the alert stays on the screen it came from.
    }
  }

  /**
   * Withdraws notifications this page posted. Takes the notifier's `{ identifiers }`, and a bare
   * identifier as well, so a screen can say `remove(id)`.
   */
  async remove(argument) {
    const identifiers = Array.isArray(argument?.identifiers)
      ? argument.identifiers
      : [argument].filter((one) => typeof one === 'string')
    for (const identifier of identifiers) {
      this.live.get(identifier)?.close?.()
      this.live.delete(identifier)
    }
    const registration = await this.workerRegistration()
    if (registration == null) return
    for (const identifier of identifiers) {
      try {
        for (const one of await registration.getNotifications({ tag: identifier })) one.close()
      } catch {
        /* The worker went away; nothing is left to close. */
      }
    }
  }

  /** What is on screen now, so a replacement is silent and a withdrawal is not posted twice. */
  async deliveredIdentifiers() {
    const registration = await this.workerRegistration()
    if (registration != null) {
      try {
        const shown = await registration.getNotifications()
        return shown.map((one) => one.tag).filter((one) => typeof one === 'string' && one.length > 0)
      } catch {
        /* Fall through to what this page knows. */
      }
    }
    return [...this.live.keys()]
  }

  // MARK: - Taps

  handleTap(userInfo) {
    if (userInfo == null || this.onTap == null) return
    this.onTap({ userInfo, at: this.now() })
  }

  /** The service worker, when there is one that can show a notification. Looked up once. */
  async workerRegistration() {
    if (this.registration != null) return this.registration
    if (this.serviceWorker?.getRegistration == null) return null
    if (this.registrationLookup == null) {
      this.registrationLookup = Promise.resolve(this.serviceWorker.getRegistration())
        .then((found) => (typeof found?.showNotification === 'function' ? found : null))
        .catch(() => null)
    }
    this.registration = await this.registrationLookup
    return this.registration
  }
}

/**
 * The `userInfo` a tap is read back from.
 *
 * Spelled here rather than imported: `WeatherAlertNotification.userInfo` lives in the weather
 * layer, and `platform` is not above it (docs/PORTING.md §9). The two spellings have to agree, so
 * this one is exported and `test/AppNotifications.test.js` asserts that they do.
 */
export function notificationUserInfo(notification) {
  return {
    type: 'weatherAlert',
    wxEvent: String(notification.identity.event),
    wxOffice: String(notification.identity.office),
    wxEtn: String(notification.identity.etn),
    wxBot: String(notification.botID),
    wxPlace: notification.placeID ?? '',
  }
}
