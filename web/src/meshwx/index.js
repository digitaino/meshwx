// The MeshWX layer: the v5 wire codec, the bundled lookup tables, place names, ZIPs, the
// outline geometry and the presentation rules (PORTING §2 — other layers import from here,
// never from a file inside this directory).
//
// `nodeLoader.js` is deliberately absent: it uses `node:fs`, which the browser has not. Tests
// import it by path.

export {
  MeshWXWire,
  MeshWXMessageType,
  MeshWXTypeNames,
  MeshWXDataSource,
  MeshWXHeader,
  hexToBytes,
  bytesToHex,
} from './MeshWXWire.js';

export {
  roundHalfToEven,
  MeshWXCompass,
  MeshWXSky,
  MeshWXCoordinate,
  MeshWXWarningIdentity,
  MeshWXTornadoTag,
  MeshWXFloodSource,
  MeshWXFloodDamage,
  MeshWXAreaRun,
  MeshWXWarning,
  MeshWXCancelReason,
  MeshWXCancel,
  MeshWXDigest,
  MeshWXStationObservation,
  MeshWXObservations,
  MeshWXForecastPeriod,
  MeshWXForecast,
  MeshWXTextSubject,
  MeshWXText,
  MeshWXNotAvailableReason,
  MeshWXNotAvailable,
  MeshWXCoverage,
  MeshWXRequest,
  MeshWXAreaSweep,
} from './MeshWXMessage.js';

export { MeshWXDecodeError, MeshWXDecoder, decode, decodeHeader } from './MeshWXDecoder.js';

export { MeshWXEncodeError, MeshWXEncoder, encode } from './MeshWXEncoder.js';

export {
  MeshWXGeo,
  MeshWXTables,
  MeshWXStation,
  MeshWXPoint,
  MeshWXPlace,
  MeshWXZone,
  MeshWXCounty,
  MeshWXOffice,
  MeshWXEventName,
  MeshWXNamedArea,
} from './MeshWXTables.js';

export { MeshWXPlaceNames } from './MeshWXPlaceNames.js';

export { MeshWXZip, zipCodeIn, parseZipRows } from './MeshWXZips.js';

export { MeshWXGeometry } from './MeshWXGeometry.js';

export {
  MeshWXSeverity,
  MeshWXEventTint,
  MeshWXConditionIcon,
  MeshWXPeriodSlot,
  MeshWXForecastLayout,
  MeshWXForecastEntry,
  MeshWXWindReading,
  MeshWXFeedHealth,
  MeshWXPresentation,
} from './MeshWXPresentation.js';
