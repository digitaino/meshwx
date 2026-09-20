// Alert notifications on the web: the poster, the app's copy, and where a tap goes
// (docs/MESHWX_UI.md §16).
//
// The Swift's `WeatherAlertNotificationCopyImpl` has no test of its own — its wording is asserted
// through the notifier's suite in the services package — so these are the web's, and they pin the
// two things the app's copy does that the weather layer's fallback does not.

import assert from 'node:assert/strict'
import { afterEach, describe, it } from 'node:test'

import {
  WeatherAlertNotification,
  WeatherAlertNotificationCopyRegistry,
  WeatherAlertNotificationSubject,
  WeatherAlertNotificationTap,
} from '../src/weather/index.js'
import { NotificationAuthorization, notificationUserInfo, WebNotifications } from '../src/platform/notifications.js'
import {
  WeatherAlertNotificationCopyImpl,
  WeatherAlertNotificationRouting,
  WeatherFormatting,
} from '../src/app/index.js'
import * as F from './helpers/app-fixture.js'

const tables = await F.loadTables()

const identity = { event: 3, office: 35, etn: 42 }

const notification = WeatherAlertNotification.make({
  identifier: 'wx.19578.3.35.42.here',
  threadIdentifier: 'wx.here',
  content: { title: 'Severe Thunderstorm Warning', subtitle: 'via WX-AUS', body: 'Austin, TX' },
  sound: true,
  identity,
  botID: 19578,
  placeID: 'here',
})

/** The browser's `Notification`, as much of it as the poster touches. */
class FakeNotification {
  static permission = 'granted'
  /** What the browser's own prompt would answer. */
  static grants = 'granted'
  static asked = 0
  static posted = []

  static requestPermission() {
    FakeNotification.asked += 1
    FakeNotification.permission = FakeNotification.grants
    return Promise.resolve(FakeNotification.permission)
  }

  constructor(title, options) {
    this.title = title
    this.options = options
    this.closed = false
    FakeNotification.posted.push(this)
  }

  close() {
    this.closed = true
    this.onclose?.()
  }
}

afterEach(() => {
  FakeNotification.permission = 'granted'
  FakeNotification.grants = 'granted'
  FakeNotification.asked = 0
  FakeNotification.posted = []
  WeatherAlertNotificationRouting.reset()
  WeatherAlertNotificationCopyRegistry.install(null)
})

describe('WebNotifications', () => {
  it('reports the browser permission in the words the app speaks', () => {
    const poster = new WebNotifications({ notification: FakeNotification })
    FakeNotification.permission = 'granted'
    assert.equal(poster.authorization, NotificationAuthorization.authorized)
    FakeNotification.permission = 'denied'
    assert.equal(poster.authorization, NotificationAuthorization.denied)
    FakeNotification.permission = 'default'
    assert.equal(poster.authorization, NotificationAuthorization.notDetermined)
    assert.equal(new WebNotifications({ notification: null }).authorization, NotificationAuthorization.unsupported)
  })

  it('asks the browser once, from the tap that turned a bell on', async () => {
    const poster = new WebNotifications({ notification: FakeNotification })
    FakeNotification.permission = 'default'
    FakeNotification.grants = 'denied'
    assert.equal(await poster.request(), false)
    assert.equal(poster.authorization, NotificationAuthorization.denied)

    FakeNotification.permission = 'default'
    FakeNotification.grants = 'granted'
    assert.equal(await poster.request(), true)
    assert.equal(FakeNotification.asked, 2)
    // Already granted: nothing is asked again.
    assert.equal(await poster.request(), true)
    assert.equal(FakeNotification.asked, 2)
  })

  it('posts under the identifier as its tag, so a repeat replaces rather than stacks', async () => {
    const poster = new WebNotifications({ notification: FakeNotification })
    await poster.post(notification)
    assert.equal(FakeNotification.posted.length, 1)
    const posted = FakeNotification.posted[0]
    assert.equal(posted.title, 'Severe Thunderstorm Warning')
    assert.equal(posted.options.tag, notification.identifier)
    assert.equal(posted.options.body, 'via WX-AUS\nAustin, TX')
    assert.equal(posted.options.silent, false)
    assert.deepEqual(await poster.deliveredIdentifiers(), [notification.identifier])

    // A silent delivery: a replacement, or the rank-6 toggle.
    await poster.post({ ...notification, identifier: 'wx.other', sound: false })
    assert.equal(FakeNotification.posted[1].options.silent, true)
  })

  it('posts nothing while the browser has not granted permission', async () => {
    FakeNotification.permission = 'denied'
    const poster = new WebNotifications({ notification: FakeNotification })
    await poster.post(notification)
    assert.equal(FakeNotification.posted.length, 0)
  })

  it('a tap carries the userInfo the tap reader expects', async () => {
    const taps = []
    const poster = new WebNotifications({
      notification: FakeNotification,
      onTap: (tap) => taps.push(tap),
      now: () => F.now,
      focus: () => {},
    })
    await poster.post(notification)
    FakeNotification.posted[0].onclick()
    assert.equal(taps.length, 1)
    assert.equal(taps[0].at, F.now)
    // The exact object the weather layer writes, so `WeatherAlertNotificationTap.receive` reads it.
    assert.deepEqual(taps[0].userInfo, WeatherAlertNotification.userInfo(notification))
  })

  it('the userInfo spelled in the platform layer is the weather layer own', () => {
    assert.deepEqual(notificationUserInfo(notification), WeatherAlertNotification.userInfo(notification))
  })

  it('withdrawing closes what this page posted', async () => {
    const poster = new WebNotifications({ notification: FakeNotification })
    await poster.post(notification)
    await poster.remove({ identifiers: [notification.identifier] })
    assert.equal(FakeNotification.posted[0].closed, true)
    assert.deepEqual(await poster.deliveredIdentifiers(), [])

    // A screen may say `remove(id)`.
    await poster.post(notification)
    await poster.remove(notification.identifier)
    assert.deepEqual(await poster.deliveredIdentifiers(), [])
  })
})

describe('WeatherAlertNotificationCopy', () => {
  const copy = WeatherAlertNotificationCopyImpl.make({ locale: F.locale, timeZone: F.timeZone })

  const subject = (options = {}) =>
    WeatherAlertNotificationSubject.make({
      warning: F.warning({
        event: 3,
        office: 35,
        etn: 42,
        expiresMinutes: Math.floor(F.now / 60_000) + 40,
        tornado: 2,
      }),
      placeLabel: 'Austin, TX',
      placement: { kind: 'here' },
      botName: 'WX-AUS',
      now: F.now,
      ...options,
    })

  it('names the place the way every other screen does, with its state on', () => {
    const content = copy.content(subject(), { tables })
    assert.equal(F.plain(content.body), 'Austin, TX · until 12:00 AM · Tornado: radar indicated')
    assert.equal(content.subtitle, 'National Weather Service alerts via WX-AUS')
    assert.equal(content.title, WeatherFormatting.eventName(3, { tables }))
  })

  it('a warning near the place leads with how far it is', () => {
    const content = copy.content(
      subject({ placement: { kind: 'near', kilometres: 25, direction: 0 } }),
      { tables },
    )
    assert.ok(F.plain(content.body).startsWith('25 km N of Austin, TX · until '), content.body)
  })

  it('a warning drained from the radio queue says it arrived late', () => {
    const content = copy.content(subject({ isLate: true }), { tables })
    assert.ok(content.body.endsWith('\nReceived late — sent while your radio was out of range.'), content.body)
  })

  it('with no bot name the subtitle is the generic one', () => {
    const content = copy.content(subject({ botName: null }), { tables })
    assert.equal(content.subtitle, 'National Weather Service alerts via weather radios')
  })

  it('my location is named by the string table, not by a coordinate', () => {
    assert.equal(copy.myLocationLabel, 'your location')
  })
})

describe('WeatherAlertNotificationRouting', () => {
  it('installs the app copy and opens the tool when a tap lands', () => {
    const tap = new WeatherAlertNotificationTap()
    let opened = 0
    const stop = WeatherAlertNotificationRouting.install({ open: () => (opened += 1), tap })
    assert.equal(WeatherAlertNotificationRouting.isInstalled, true)
    assert.notEqual(WeatherAlertNotificationCopyRegistry.current, null)

    WeatherAlertNotificationRouting.receive({
      userInfo: WeatherAlertNotification.userInfo(notification),
      at: F.now,
      tap,
    })
    assert.equal(opened, 1)
    assert.deepEqual(tap.pending?.identity, identity)
    // And the tool takes it, once, while it is fresh.
    assert.notEqual(tap.take({ now: F.now + 1000 }), null)
    assert.equal(tap.take({ now: F.now + 1000 }), null)

    stop()
    assert.equal(WeatherAlertNotificationRouting.isInstalled, false)
  })

  it('a tap that landed before the shell was ready still opens the tool', () => {
    const tap = new WeatherAlertNotificationTap()
    tap.receive({ userInfo: WeatherAlertNotification.userInfo(notification), at: F.now })
    let opened = 0
    WeatherAlertNotificationRouting.install({ open: () => (opened += 1), tap })
    assert.equal(opened, 1)
  })

  it('wires the poster, so a tap on a notification reaches the tool', async () => {
    const tap = new WeatherAlertNotificationTap()
    const poster = new WebNotifications({ notification: FakeNotification, now: () => F.now, focus: () => {} })
    assert.equal(poster.onTap, null)
    WeatherAlertNotificationRouting.install({ tap, notifications: poster })
    await poster.post(notification)
    FakeNotification.posted[0].onclick()
    assert.deepEqual(tap.pending?.identity, identity)
  })

  it('anything that is not one of ours is ignored', () => {
    const tap = new WeatherAlertNotificationTap()
    let opened = 0
    WeatherAlertNotificationRouting.install({ open: () => (opened += 1), tap })
    WeatherAlertNotificationRouting.receive({ userInfo: { type: 'chat' }, at: F.now, tap })
    assert.equal(opened, 0)
    assert.equal(tap.pending, null)
  })
})
