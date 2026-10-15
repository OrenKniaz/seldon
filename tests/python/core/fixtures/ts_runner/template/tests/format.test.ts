import test from 'node:test';
import assert from 'node:assert/strict';
import { format } from '../src/format.ts';

test('format appends units', () => {
  assert.equal(format(5), '5 units');
});
