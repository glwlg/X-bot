import assert from 'node:assert/strict'
import test from 'node:test'

import {
    formatRecordTimeForInput,
    serializeRecordTimeInput,
    toLocalIsoString,
} from '../src/utils/accountingDateTime.ts'

test('serializeRecordTimeInput keeps the local clock time from datetime-local inputs', () => {
    assert.equal(serializeRecordTimeInput('2026-03-14T19:25'), '2026-03-14T19:25')
})

test('toLocalIsoString formats dates with local time instead of UTC-shifting them', () => {
    const date = new Date('2026-03-14T19:25:42+08:00')

    assert.equal(
        toLocalIsoString(date, { includeSeconds: true }),
        '2026-03-14T19:25:42',
    )
})

test('formatRecordTimeForInput returns a datetime-local compatible value', () => {
    assert.equal(
        formatRecordTimeForInput('2026-03-14T19:25:42'),
        '2026-03-14T19:25',
    )
})

test('formatRecordTimeForInput converts legacy UTC timestamps into local input time', () => {
    assert.equal(
        formatRecordTimeForInput('2026-03-14T11:25:00.000Z'),
        '2026-03-14T19:25',
    )
})

test('invalid record time values fail closed', () => {
    assert.equal(serializeRecordTimeInput('not-a-time'), '')
    assert.equal(formatRecordTimeForInput('not-a-time'), '')
})
