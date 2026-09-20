// A simulated companion radio for development: the whole radio path with no radio.
//
// `FakeRadioTransport` speaks the companion protocol the way the firmware does. This host feeds
// it what the bot's debug bridge reports as transmitted, as `GRP_DATA` on the slot holding
// `#meshwx`, and forwards every Request datagram the app sends to the bridge as the `>` text it
// carries. Everything between the bridge and the screen is then the real thing: the session's
// handshake and drain, the contact and channel reads, `SessionWeatherTransport`'s slot search
// and its Request datagram, the service's pairing of answers to requests.
//
// The slot is 31 of 40, where the owner's own radio keeps `#meshwx`: the first cut of the iOS
// tool scanned only slots 0 to 7 and every request fell back to a DM.
import { FakeRadioTransport, makeFakeContact } from '../radio/index.js'
import { decode } from '../meshwx/index.js'
import { WeatherChannel } from '../weather/index.js'
import { RemoteBotWeatherTransport } from './RemoteBotWeatherTransport.js'

export const SIMULATED_WEATHER_SLOT = 31

/**
 * @param {object} [options]
 * @param {boolean} [options.withWeatherChannel] false to start without `#meshwx`, to try the
 *   prompt that adds it
 * @param {(message: string) => void} [options.log]
 * @returns {Promise<FakeRadioTransport>} ready to hand to a `MeshCoreSession`
 */
export async function makeSimulatedRadio({ withWeatherChannel = true, log = () => {} } = {}) {
  const bridge = new RemoteBotWeatherTransport({ clientID: 'web-simradio', log })
  const link = await bridge.linkState()
  if (!link?.bot) throw new Error('the development bridge is not answering')

  const channels = new Map([[0, { name: 'Public', secret: new Uint8Array(16).fill(0x11) }]])
  if (withWeatherChannel) channels.set(SIMULATED_WEATHER_SLOT, { name: WeatherChannel.name, secret: await WeatherChannel.secret() })

  const radio = new FakeRadioTransport({
    name: 'Simulated radio', model: 'Simulated', firmwareVersion: 11, maxChannels: 40, channels,
    contacts: [makeFakeContact({ publicKey: link.bot.publicKey, advertisedName: link.bot.name, lastAdvertisement: Date.now() })],
  })

  let unsubscribe = null
  const connect = radio.connect.bind(radio)
  radio.connect = async () => {
    await connect()
    unsubscribe = bridge.subscribeDatagrams((datagram) => {
      const slot = [...radio.channels].find(([, channel]) => channel.name === WeatherChannel.name)?.[0]
      if (slot == null) return                       // a radio without the channel hears nothing
      radio.deliverChannelData({ channelIndex: slot, dataType: datagram.dataType, data: datagram.data, snr: datagram.snr, pathLength: 0xff })
    })
  }
  const disconnect = radio.disconnect.bind(radio)
  radio.disconnect = async () => { unsubscribe?.(); unsubscribe = null; await disconnect() }

  radio.onChannelData(({ payload }) => {
    try {
      const message = decode(payload)
      if (message.name !== 'request') return
      log(`simulated radio: flooding ${message.text}`)
      bridge.sendChannelRequest({ text: message.text, botID: message.bot, seq: message.seq }).catch((error) => log(`simulated radio: ${error.message}`))
    } catch (error) { log(`simulated radio: undecodable datagram (${error.message})`) }
  })
  return radio
}
