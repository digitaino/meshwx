// Port of MC1Services/Sources/MC1Services/Services/Weather/Screen/WeatherGeo.swift

import { MeshWXCompass, MeshWXGeo, MeshWXGeometry } from '../meshwx/index.js'

/** Small geometry helpers the screen types share. */
export const WeatherGeo = Object.freeze({
  /** `kilometres(_ from:, _ to:)`: both parameters are unlabelled, so both stay positional. */
  kilometres(from, to) {
    return MeshWXGeo.distanceKilometres({
      fromLat: from.latitude,
      fromLon: from.longitude,
      toLat: to.latitude,
      toLon: to.longitude,
    })
  },

  /** The vertex average: a label anchor, not a true centroid, which is all a direction needs. */
  centre({ of }) {
    const coordinates = of ?? []
    if (coordinates.length === 0) return null
    const latitude = coordinates.reduce((total, one) => total + one.latitude, 0) / coordinates.length
    const longitude = coordinates.reduce((total, one) => total + one.longitude, 0) / coordinates.length
    return { latitude, longitude }
  },

  /** The 16-point compass direction from one coordinate towards another. */
  direction({ from, to }) {
    const lat1 = (from.latitude * Math.PI) / 180
    const lat2 = (to.latitude * Math.PI) / 180
    const dLon = ((to.longitude - from.longitude) * Math.PI) / 180
    const y = Math.sin(dLon) * Math.cos(lat2)
    const x = Math.cos(lat1) * Math.sin(lat2) - Math.sin(lat1) * Math.cos(lat2) * Math.cos(dLon)
    const degrees = (((Math.atan2(y, x) * 180) / Math.PI + 360) % 360)
    return MeshWXCompass.make({ degrees })
  },
})

/**
 * What alert placement needs from the zone and county outlines.
 *
 * The Swift protocol `WeatherAreaGeometry`, which is why a test can say "still loading" without
 * waiting on a 10 MB parse. In JS it is a shape, not a type: any object with `isLoaded`,
 * `distanceKilometres({ from, toArea })` and `centre({ ofArea })` is one.
 */
export const WeatherAreaGeometry = Object.freeze({
  /** The port of `extension MeshWXGeometry: WeatherAreaGeometry`. */
  of(geometry) {
    return {
      get isLoaded() {
        return geometry.isZoneFileLoaded && geometry.isCountyFileLoaded
      },
      distanceKilometres({ from, toArea }) {
        return geometry.distanceKilometres({ from, toArea })
      },
      centre({ ofArea }) {
        const rings = geometry.rings({ for: ofArea })
        if (rings == null) return null
        return WeatherGeo.centre({ of: rings.flat() })
      },
    }
  },

  /** The outlines as `MeshWXGeometry.shared` holds them. */
  shared() {
    return WeatherAreaGeometry.of(MeshWXGeometry.shared)
  },
})
