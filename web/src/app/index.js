// The app layer: the port of MC1/Views/Tools/Weather's model, builder, copy and formatting.
//
// The screens import from here, never from a file inside this directory (docs/PORTING.md §2).
// This layer may use browser APIs, but only inside functions and behind injected dependencies:
// everything the app around the tool provides comes through one `WeatherHost`.

export { WeatherFormatting, WeatherDataSource } from './WeatherFormatting.js'

export { WeatherReferenceNames } from './WeatherReferenceNames.js'

export {
  WeatherAlertStatusLine,
  WeatherAlertStatusLineAction,
  WeatherAnswerNote,
  WeatherAnswerNoteKind,
  WeatherCopy,
  WeatherPlaceState,
} from './WeatherCopy.js'

export {
  WeatherAreaName,
  WeatherBotRow,
  WeatherBuildRequest,
  WeatherBuildResult,
  WeatherPageBuild,
  WeatherPlaceFacts,
  WeatherPlaceInput,
  WeatherScreenBuilder,
  WeatherScreenContext,
} from './WeatherScreenBuilder.js'

export {
  WeatherAlertTarget,
  WeatherPageScreen,
  WeatherPlacePickerAction,
  WeatherStationTarget,
  WeatherTaskHolder,
  WeatherToolModel,
} from './WeatherToolModel.js'

export { WeatherRequestLogStore } from './WeatherRequestLogStore.js'

export {
  WeatherAlertNotificationCopy,
  WeatherAlertNotificationCopyImpl,
} from './WeatherAlertNotificationCopy.js'

export { WeatherAlertNotificationRouting } from './WeatherAlertNotificationRouting.js'

export {
  FakeLocationService,
  FakeNotifications,
  FakeWeatherHost,
  WeatherDefaults,
  WeatherHostPlaceRules,
} from './WeatherHost.js'
