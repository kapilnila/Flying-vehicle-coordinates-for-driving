import {expect, test} from '@playwright/test';

test('public hosted dashboard needs no token and keeps operator logs private', async ({page, request}) => {
  test.skip(process.env.PUBLIC_API_TEST !== '1', 'requires a public hosted service');
  expect((await request.get('/v1/config')).ok()).toBeTruthy();
  await page.goto('/');
  await expect(page.getByLabel('API token', {exact: true})).toHaveCount(0);
  await expect(page.getByText('1 backend ready', {exact: true})).toBeVisible();
  const vehicle = 'public-' + Date.now();
  await page.getByLabel('vehicle_id', {exact: true}).fill(vehicle);
  await page.getByRole('button', {name: 'Generate IDs →'}).click();
  await expect(page.getByRole('status')).toContainText('Committed');
  await page.getByRole('button', {name: /History/}).click();
  await page.getByRole('button', {name: 'Search history'}).click();
  await expect(page.locator('tbody tr')).toHaveCount(1);
  const download = page.waitForEvent('download');
  await page.getByRole('button', {name: 'Export JSONL'}).click();
  expect((await download).suggestedFilename()).toBe('history.jsonl');
  await page.getByRole('button', {name: /Paths/}).click();
  await page.getByRole('button', {name: 'Live map', exact: true}).click();
  await expect(page.getByText('Following ' + vehicle, {exact: true})).toBeVisible();
  await page.getByRole('button', {name: /History/}).click();
  await page.getByRole('button', {name: 'Request log', exact: true}).click();
  await expect(page.getByLabel('Operator token')).toBeVisible();
  expect((await request.get('/v1/operator/request-logs')).status()).toBe(401);
});
