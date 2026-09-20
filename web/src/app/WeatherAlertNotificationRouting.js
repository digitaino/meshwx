// Port of MC1/Views/Tools/Weather/WeatherAlertNotificationRouting.swift (docs/PORTING.md).

import { WeatherAlertNotificationCopyRegistry, WeatherAlertNotificationTap } from '../weather/index.js'
import { WeatherAlertNotificationCopy } from './WeatherAlertNotificationCopy.js'

/**
 * What a tapped alert notification does to the app (docs/MESHWX_UI.md §16).
 *
 * Installed once at boot: the notification poster is per connection and the Weather tool's model
 * lives for one visit, so neither of them can be what a tap lands on.
 * `WeatherAlertNotificationTap` holds what was tapped; this turns it into navigation, and the
 * tool takes the alert itself when it next appears (`WeatherToolModel.appear`).
 *
 * The Swift selects the Tools tab and names Weather as the open tool. The web has no tab bar, so
 * the one thing it can do is injected: `open()` is whatever brings the Weather tool to the front
 * (`app.nav` popping to the root, a route change). With the tool already on screen there is
 * nothing to do — the model's own `isVisible` handler takes the tap.
 *
 * The other deviation: Swift re-arms `withObservationTracking` after every change.
 * `WeatherAlertNotificationTap.subscribe` stays subscribed, so there is nothing to re-arm.
 */
export const WeatherAlertNotificationRouting = Object.freeze({
  /**
   * Installs the localized notification copy and starts watching for taps. Safe to call again:
   * the boot task runs on every appearance of the shell. Returns the function that stops
   * watching, for a test.
   */
  install({
    open = null,
    copy = WeatherAlertNotificationCopy,
    tap = WeatherAlertNotificationTap.shared,
    notifications = null,
  } = {}) {
    WeatherAlertNotificationCopyRegistry.install(copy)
    // Re-arming rather than refusing: the Swift guards because `withObservationTracking` fires
    // once, and here the second caller is the one that knows how to bring the tool forward.
    installed?.stop()
    const unsubscribe = tap.subscribe((target) => {
      if (target != null) open?.()
    })
    if (notifications != null) {
      notifications.onTap = ({ userInfo, at }) =>
        WeatherAlertNotificationRouting.receive({ userInfo, at, tap })
      notifications.start?.()
    }
    const stop = () => {
      if (installed?.stop !== stop) return
      unsubscribe()
      installed = null
    }
    installed = { stop }
    // A tap that landed before the shell was ready still opens the tool.
    if (tap.pending != null) open?.()
    return stop
  },

  /**
   * A tapped notification's `userInfo`, on its way to the tool. `src/platform/notifications.js`
   * calls this from its click handler; anything that is not one of ours is ignored.
   */
  receive({ userInfo, at, tap = WeatherAlertNotificationTap.shared }) {
    tap.receive({ userInfo, at })
  },

  /** Whether `install` has already armed the watcher. */
  get isInstalled() {
    return installed != null
  },

  /** Tears the watcher down; for tests and for a shell that is being replaced. */
  reset() {
    installed?.stop()
    installed = null
  },
})

let installed = null
