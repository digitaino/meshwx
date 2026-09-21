// The radio side of the tool: the weather radio's own page, the alerts and their maps, the
// Weather Service text reports, what the channel has carried, and the alert notifications.
//
// The place side (`src/ui/place/`) links to these by name; nothing here imports a file inside
// that directory except through its `index.js`, and only lazily, at the moment of a push.
//
// Every factory takes `{ app, page, … }` and returns a screen (docs/UI_KIT.md). `page` is the
// `WeatherPageScreen` the user came from: a pushed screen answers for **that** page and never
// for "the model's current page" (docs/MESHWX_UI.md §3.1 P-1, U-18).

export { WeatherRadioScreen } from './WeatherRadioView.js'
export { WeatherAlertsListScreen } from './WeatherAlertsListView.js'
export { WeatherAlertDetailScreen } from './WeatherAlertDetailView.js'
export { WeatherReportScreen, WeatherStatePickerScreen, WeatherReportProduct, WeatherReportText, NarrativeBody } from './WeatherReportsView.js'
export {
  WeatherAreaMapScreen, WeatherAreaFullMapScreen, WeatherAreaListScreen, WeatherAreaDetailScreen,
  WeatherAreaMapCopy, WeatherAreaMapList, WeatherAreaMapScope, pictureOf,
} from './WeatherAreaMapView.js'
export { WeatherAreaPickerScreen } from './WeatherAreaPickerView.js'
export { WeatherTrafficScreen, WeatherTrafficDetailScreen } from './WeatherTrafficView.js'
export { WeatherRequestsScreen, RequestRow } from './WeatherRequestsView.js'
export { WeatherCachedScreen } from './WeatherCachedView.js'
export { WeatherRadarScreen, radarDrawing, radarPrint, RADAR_WIDTHS } from './WeatherRadarView.js'
export { WeatherAlertNotificationsScreen } from './WeatherAlertNotificationsView.js'
export { RadioPill } from './RadioPill.js'
export { RadioSettingsScreen } from './RadioSettingsView.js'

// Shared with the place side: the alert row and the ask button, so a place page's alert strip
// and its Update control read as the same tool.
export { AlertRow, AlertStatusRow } from './WeatherAlertRow.js'
export { AskButton, AskFootnotes, PendingBar, PendingOverlay, isAskable } from './WeatherAskControl.js'
export { WeatherMapDrawing, WeatherAreaMapDrawing, WeatherAlertFullMapScreen, MapCard, MapView, applyDrawing, preloadGeometry, isGeometryLoaded, tintOf } from './WeatherAlertMap.js'
export { copy, safeScreen, Segmented, Line, Loading, Headline } from './support.js'
