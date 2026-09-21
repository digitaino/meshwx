// The radio layer: the MeshCore companion protocol as a browser can speak it.
//
// Import from here, never from a file inside this directory (docs/PORTING.md §2).
//
// ## Shape
//
//   transport (BLE | serial | fake)  →  MeshCoreSession  →  events + command methods
//
// A transport moves frames: `connect()`, `disconnect()`, `send(frame)`, `subscribe(fn)`,
// and an `onDisconnect` property. A frame is a bare companion payload — the transport owns
// whatever framing its medium needs (a length prefix on serial, nothing on BLE).
//
// ## Events the session emits
//
// Every event is `{ kind, … }`. Two families arrive on the one `subscribe(fn)`:
//
// **Wire events** — one per frame the radio sent, carrying the Swift `MeshEvent` case name:
//
//   ok                     { value: number|null }
//   error                  { code: number|null }
//   selfInfo               { value: SelfInfo }
//   deviceInfo             { value: DeviceCapabilities }
//   battery                { value: BatteryInfo }
//   currentTime            { value: ms }
//   disabled               { reason: string }
//   contactsStart          { count }
//   contact                { value: MeshContact }
//   contactsEnd            { lastModified: ms }
//   contactURI             { value: string }
//   contactDeleted         { publicKey: Uint8Array }
//   contactsFull           { }
//   newContact             { value: MeshContact }
//   messageSent            { value: { route, expectedAck, suggestedTimeoutMs } }
//   contactMessageReceived { value: ContactMessage }
//   channelMessageReceived { value: ChannelMessage }
//   channelDataReceived    { value: ChannelDatagram }   ← the weather layer's input
//   noMoreMessages         { }
//   messagesWaiting        { }
//   acknowledgement        { code: Uint8Array, tripTime: number|null }
//   advertisement          { publicKey: Uint8Array }
//   pathUpdate             { publicKey: Uint8Array }
//   channelInfo            { value: ChannelInfo }
//   statsCore/statsRadio/statsPackets { value }
//   parseFailure           { data: Uint8Array, reason: string }
//   unknown                { code: number, name: string|null, data: Uint8Array }
//
// **Session events** — the session's own:
//
//   connectionStateChanged { value: 'disconnected'|'connecting'|'connected' }
//   contactMessage         { message: ContactMessage, isDrainingBacklog: boolean }
//   channelMessage         { message: ChannelMessage, isDrainingBacklog: boolean }
//   channelData            { datagram: ChannelDatagram, isDrainingBacklog: boolean }
//   pollFailed             { error: Error, isDrainingBacklog: boolean }
//   deviceTimeSynced       { driftSeconds: number }
//   deviceTimeSyncFailed   { error: Error }
//
// The three message events are what the poller pulled out of the radio's queue, one per
// message. They are emitted for *every* message, weather or not: a browser tab that is the
// radio's companion receives the user's chats too, and a chat this client has no screen for
// must still reach the UI so the user knows it arrived here. A datagram therefore appears
// twice — once as the `channelDataReceived` frame, once as the poller's `channelData` —
// exactly as the Swift dispatches the event and hands the `MessageResult` to its poller.

export * from './Bytes.js'
export * from './Frames.js'
export * from './PacketCodes.js'
export * from './PacketBuilder.js'
export * from './PacketParser.js'
export * from './RadioActivity.js'
export * from './RadioParameters.js'
export * from './RadioPresets.js'
export * from './MeshCoreSession.js'
export * from './FakeRadioTransport.js'
export * from './WebBluetoothTransport.js'
export * from './WebSerialTransport.js'
