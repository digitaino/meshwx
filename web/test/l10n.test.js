import test from 'node:test'
import assert from 'node:assert/strict'
import { t, format } from '../src/l10n.js'

test('reads the iOS table', () => {
  assert.equal(t('weather.title'), 'Weather')
  assert.equal(t('weather.bot.heardOnly', '041D'), 'Weather radio 041D')
})

test('positional and typed specifiers', () => {
  assert.equal(format('%2$@ then %1$@', ['a', 'b']), 'b then a')
  assert.equal(format('%d of %lld, %.1f%%', [3, 7, 2.345]), '3 of 7, 2.3%')
})

test('an unknown key throws under test', () => {
  assert.throws(() => t('weather.no.such.key'))
})
