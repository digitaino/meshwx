// The place side of the Weather tool: the pager of places, a place page, the conditions hero, the
// forecast, Update, Places and the stations screens (docs/MESHWX_UI.md §4, §5, §8, §9, §11, §12).
//
// One module per Swift view file of `MC1/Views/Tools/Weather/`, same base name (docs/PORTING.md
// §2), plus `support.js` for the two things every one of them needs: the bridge to `src/app/` and
// the rule that a pushed screen answers for the page it was opened from.
//
// The entry point is `WeatherToolScreen({ app })`, the tool's root screen. The radio side imports
// `WeatherStationScreen({ app, page, index })` from here.

export { WeatherToolScreen } from './WeatherToolView.js'

export {
  WeatherPlacePageView,
  WeatherWarningBannerSection,
  WeatherRadioRowSection,
  WeatherRadioBannerSection,
} from './WeatherPlacePageView.js'

export { WeatherConditionsSection } from './WeatherConditionsSection.js'

export { WeatherForecastSection, WeatherForecastRowView } from './WeatherForecastSection.js'

export { WeatherRadarSection } from './WeatherRadarSection.js'

export { WeatherUpdateControl, updateCaption } from './WeatherUpdateControl.js'

export { WeatherAskButton, WeatherAskFootnotes, WeatherPendingBar, requestKeys } from './WeatherAskControl.js'

export { WeatherPlacePickerView, openPlaces, looksLikeStationCode } from './WeatherPlacePickerView.js'

export { WeatherStationsScreen, WeatherStationScreen } from './WeatherStationsView.js'

export { screenFor, pageIDOf, setAppNamespace, loadAppNamespace } from './support.js'
