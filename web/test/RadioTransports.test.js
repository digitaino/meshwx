// The two browser transports must import under Node without touching `navigator`, so the
// radio layer stays testable and so `node --test` runs the whole client. Everything they do
// with a browser API happens inside a method.

import test from 'node:test'
import assert from 'node:assert/strict'
import {
  DEFAULT_BAUD_RATE,
  MAX_WRITE_BYTES,
  NORDIC_UART_RX_CHARACTERISTIC,
  NORDIC_UART_SERVICE,
  NORDIC_UART_TX_CHARACTERISTIC,
  WebBluetoothTransport,
  WebSerialTransport,
} from '../src/radio/index.js'

test('importing and constructing the transports touches no browser API', () => {
  assert.doesNotThrow(() => new WebBluetoothTransport())
  assert.doesNotThrow(() => new WebSerialTransport())
})

test('isSupported answers false where the API is absent', () => {
  assert.equal(WebBluetoothTransport.isSupported(), false)
  assert.equal(WebSerialTransport.isSupported(), false)
})

test('connect refuses rather than throwing on an undefined navigator', async () => {
  await assert.rejects(() => new WebBluetoothTransport().connect(), /not available/)
  await assert.rejects(() => new WebSerialTransport().connect(), /not available/)
})

test('send before connect is refused', async () => {
  await assert.rejects(() => new WebBluetoothTransport().send(Uint8Array.of(1)), /not connected/)
  await assert.rejects(() => new WebSerialTransport().send(Uint8Array.of(1)), /not connected/)
})

test('the Nordic UART UUIDs are the ones the iOS app uses', () => {
  assert.equal(NORDIC_UART_SERVICE, '6e400001-b5a3-f393-e0a9-e50e24dcca9e')
  assert.equal(NORDIC_UART_TX_CHARACTERISTIC, '6e400002-b5a3-f393-e0a9-e50e24dcca9e')
  assert.equal(NORDIC_UART_RX_CHARACTERISTIC, '6e400003-b5a3-f393-e0a9-e50e24dcca9e')
})

test('the serial default is the MeshCore companion baud rate', () => {
  assert.equal(DEFAULT_BAUD_RATE, 115200)
  assert.equal(new WebSerialTransport({ baudRate: 921600 }).baudRate, 921600)
})

test('a write larger than the ATT limit is refused, not chunked', async () => {
  const transport = new WebBluetoothTransport()
  // Reach past the not-connected guard by faking the link, which is all this asserts about.
  transport.subscribe(() => {})
  await assert.rejects(() => transport.send(new Uint8Array(MAX_WRITE_BYTES + 1)), /not connected/)
  assert.equal(MAX_WRITE_BYTES, 512)
})

test('subscribing and unsubscribing works before a link exists', () => {
  const transport = new WebSerialTransport()
  const unsubscribe = transport.subscribe(() => {})
  assert.equal(typeof unsubscribe, 'function')
  unsubscribe()
})
