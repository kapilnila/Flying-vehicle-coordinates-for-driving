import {expect, test} from '@playwright/test';

test('operator can trace a retry to the original batch in History', async ({page, request}) => {
  test.skip(!process.env.OPERATOR_TOKEN, 'requires a configured operator token');
  const api = process.env.API_TOKEN;
  const vehicle = 'log-' + Date.now();
  const headers = {'Idempotency-Key': vehicle, ...(api ? {Authorization: 'Bearer ' + api} : {})};
  const data = {vehicle_id: vehicle, latitude: 1, longitude: 2, speed_mps: 3, altitude_m: 4, count: 2};
  const first = await request.post('/v1/ids', {data, headers});
  expect(first.status()).toBe(201);
  const second = await request.post('/v1/ids', {data, headers});
  expect(second.status()).toBe(200);
  await page.goto('/');
  await page.getByRole('button', {name: /History/}).click();
  await page.getByRole('button', {name: 'Request log', exact: true}).click();
  await page.getByLabel('Vehicle ID', {exact: true}).fill(vehicle);
  await page.getByLabel('Operator token').fill('invalid-test-token');
  await page.getByRole('button', {name: 'Search requests'}).click();
  await expect(page.getByRole('alert')).toContainText('valid operator token');
  await page.getByLabel('Operator token').fill(process.env.OPERATOR_TOKEN!);
  await page.getByRole('button', {name: 'Search requests'}).click();
  await expect(page.getByRole('cell', {name: first.headers()['x-request-id']})).toBeVisible();
  await page.getByRole('button', {name: 'Replayed · 2 points'}).click();
  for (const id of (await first.json()).ids) {
    await expect(page.getByRole('cell', {name: id})).toBeVisible();
  }
  await page.getByLabel('Operator token').fill('');
  await expect(page.getByRole('table')).toHaveCount(0);
});
