// The radar screen (revision 11 §3), pushed from the place page's Radar card.
//
// One map, framed on the tile, and everything the picture says about the place under it: the
// legend, the time, the summary, and a control that picks the width — Local, Regional, Wide, the
// same three words the traffic log uses, never kilometres (a tile is two degrees, which is 222 km
// tall everywhere and a different width at every latitude).
//
// Two rules the drawing follows and the alert map does not:
//
// - **The alerts are outlines here.** This device may be holding a Flood Warning over the same
//   ground, and a filled polygon over the cells would hide the very thing the screen is for. The
//   shapes go on unfilled, above the cells, so both can be read at once.
// - **Unknown cells are hatched, never left looking dry.** Outside a partial tile's bounds there
//   is no reading at all, and the line under the map says so in words as well.
//
// It is bound to the page it was opened from (docs/MESHWX_UI.md §3.1 U-18): the place, the tiles
// and the bot all come from that page's snapshot, and never from whichever page the pager has
// since landed on.
import { h } from '../kit/dom.js'
import { Card, List } from '../kit/components.js'
import { cameraBox } from '../kit/MapCanvas.js'
import { t } from '../../l10n.js'
import { MeshWXRadarLevel, MeshWXRadarTile, MeshWXWarning } from '../../meshwx/index.js'
import {
  WeatherRadarCard, WeatherRadarCells, WeatherRadarPicture,
} from '../../screen/index.js'
import { copy, Line, safeScreen, Segmented } from './support.js'
import { AskButton, AskFootnotes, isAskable, PendingBar } from './WeatherAskControl.js'
import { applyDrawing, MapView, tintOf } from './WeatherAlertMap.js'

/** The three widths the screen offers. Zoom 3 is on the wire and is not one of them (§3). */
export const RADAR_WIDTHS = Object.freeze([0, 1, 2])

export function WeatherRadarScreen({ app, page }) {
  const state = { map: null, drawn: null, app }
  // Opened on the width the card was showing, so pushing the card shows the same picture rather
  // than an empty Local square. A tile somebody asked for at zoom 3 is drawn on the card and has
  // no button here, so it opens on the widest that is offered.
  const opened = page?.snapshot?.radar?.picture?.stored?.tile?.zoom ?? 0
  let zoom = Math.min(Math.max(opened, 0), RADAR_WIDTHS[RADAR_WIDTHS.length - 1])

  return safeScreen({
    id: 'radar',
    title: () => t('weather.radar.title'),
    render() {
      const words = copy(app, page)
      const snapshot = page?.snapshot ?? null
      const place = snapshot?.place ?? null
      const card = WeatherRadarCard.width(zoom, {
        place,
        tiles: snapshot?.radarTiles ?? [],
        now: words.now,
      })
      const held = card.kind === 'held' ? card.picture : null
      const tile = held?.stored?.tile ?? card.tile ?? WeatherRadarCard.tile({ for: place, zoom })
      const request = WeatherRadarCard.ask({ place, zoom })
      const drawing = radarDrawing({ app, snapshot, picture: held, tile })
      applyDrawing(state, drawing, radarPrint({ app, snapshot, picture: held, tile }))

      return List(
        h('div', { class: 'radar-screen', key: 'map' },
          MapView({ app, state, key: 'radar-map', interactive: true, ariaLabel: t('weather.radar.title') })),

        Card({ key: 'reading' },
          h('div', { class: 'status-line' },
            held == null
              // Not "no radar picture": this width's square has simply never been asked for, and
              // the button below is the whole of what to do about it.
              ? Line(t('weather.radar.notAsked'))
              : [
                words.radarSummary(held.summary, page?.placeName ?? '').map((line, index) =>
                  Line(line, { key: `summary-${index}` })),
                // The time carries its own caution from thirty minutes ("· Precipitation has
                // moved since."), so the tone and the sentence are one line and not two.
                Line(words.radarTime(held), { warn: held.age.isOld }),
                // Orange, like every other hole in a picture in this tool: grey cells are ground
                // the mosaic never covered, and a reader who takes them for clear weather has
                // been misled by the screen rather than by the radio.
                WeatherRadarPicture.isPartial(held) ? Line(t('weather.radar.partial'), { warn: true }) : null,
                // Quiet: half the detail is still the whole square, and the picture is not wrong
                // — it is what fitting a squall line into one packet costs.
                WeatherRadarPicture.isCoarse(held) ? Line(t('weather.radar.coarse'), { key: 'coarse' }) : null,
                Line(mosaicLine({ app, words, picture: held }), { key: 'mosaic' }),
              ])),

        Card({ label: t('weather.areaMap.legend'), key: 'legend' }, Legend()),

        Card({ key: 'width' },
          h('div', { class: 'ask-block' },
            Segmented({
              label: t('weather.radar.width'),
              value: zoom,
              options: RADAR_WIDTHS.map((one) => ({ value: one, label: words.radarWidthName(one) })),
              onchange: (next) => { zoom = next; app.nav.refresh() },
            }),
            // The cost above the button: it is what this tap is about to spend on the shared
            // channel, and a reader who has already tapped does not need telling. Always one
            // packet, at every width, held or not — a radar answer is one packet or a coarser
            // packet, never two (spec §7D).
            request != null && isAskable(app.model?.status?.({ for: request })) ? Line(t('weather.areaMap.packetsOne')) : null,
            request != null
              ? AskButton({
                app,
                page,
                title: held == null
                  ? t('weather.radar.ask', page?.sourceName ?? '')
                  : t('weather.radar.askNewer'),
                request,
              })
              : null,
            AskFootnotes({ app, page }),
            Line(t('weather.radar.ask.footnote')))),

        PendingBar({ app, requestsOnScreen: request == null ? [] : [request] }))
    },
  })
}

/**
 * "Cut from the Southern Plains mosaic. · Via GOES satellite" — which of the fourteen pictures
 * this square came out of, and how the radio got it (§12.1). Nothing at all when the bundle does
 * not know the product and the radio did not say where it came from.
 */
function mosaicLine({ app, words, picture }) {
  const parts = [
    words.radarMosaic(picture.stored.radar.product, app?.tables),
    words.dataSource(picture.stored.source),
  ].filter((one) => one != null)
  return parts.length === 0 ? null : parts.join(' · ')
}

/** The three swatches, at full strength, in the order the levels run. */
function Legend() {
  const levels = [
    [MeshWXRadarLevel.light, 'light'],
    [MeshWXRadarLevel.moderate, 'moderate'],
    [MeshWXRadarLevel.heavy, 'heavy'],
  ]
  return h('div', { class: 'legend legend--radar', key: 'legend' },
    levels.map(([level, name]) => h('span', {
      class: 'legend__item',
      key: name,
      style: `--tint: var(--radar-${name})`,
    }, h('span', { class: 'legend__swatch' }), h('span', null, t(`weather.radar.level.${name}`)))))
}

/**
 * What the radar map draws: the cells, the alerts as outlines over them, the place's dot, and the
 * tile as the camera box.
 *
 * The alerts are the ones this device is already holding for the page — nothing is asked for to
 * draw this screen — and they are drawn `fill: false` so the picture underneath survives them.
 */
export function radarDrawing({ app, snapshot, picture, tile }) {
  const radar = picture?.stored?.radar ?? null
  const shapes = []
  for (const item of snapshot?.alerts ?? []) {
    const polygon = MeshWXWarning.polygonCoordinates(item.warning)
    if (polygon == null || polygon.length < 3) continue
    shapes.push({
      id: `alert-${item.identity?.event}.${item.identity?.office}.${item.identity?.etn}`,
      rings: [polygon],
      tint: tintOf(item.warning.event, app?.tables),
      fill: false,
      stroke: true,
      lineWidth: 2,
      hitTest: false,
    })
  }
  const place = snapshot?.place ?? null
  return {
    shapes,
    markers: place?.coordinate == null
      ? []
      : [{ latitude: place.coordinate.latitude, longitude: place.coordinate.longitude, kind: 'place' }],
    cells: radar == null ? [] : WeatherRadarCells.rectangles({ radar }),
    unknownCells: radar == null ? [] : WeatherRadarCells.unknownRectangles({ radar }),
    // The square the packet is of, whether or not anything has been received for it: a width with
    // nothing held still shows where the tile is.
    bounds: tile == null ? null : cameraBox({
      south: tile.south,
      west: tile.west,
      north: MeshWXRadarTile.north(tile),
      east: MeshWXRadarTile.east(tile),
    }),
    padding: 8,
  }
}

/** What would change the picture, and nothing that would not. Never the clock. */
export function radarPrint({ app, snapshot, picture, tile }) {
  const radar = picture?.stored?.radar ?? null
  return [
    tile == null ? '' : MeshWXRadarTile.key(tile),
    radar?.taken_min ?? '',
    radar?.coarse === true,
    (radar?.bounds ?? []).join(','),
    (snapshot?.alerts ?? []).map((one) => `${one.identity?.event}.${one.identity?.office}.${one.identity?.etn}`).join(','),
    snapshot?.place?.coordinate?.latitude,
    snapshot?.place?.coordinate?.longitude,
    app?.basemap != null,
  ].join('|')
}
