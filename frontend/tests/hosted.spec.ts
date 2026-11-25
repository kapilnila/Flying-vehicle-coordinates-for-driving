import {test, expect} from '@playwright/test';

test('hosted dashboard authenticates vehicle operations and displays one backend', async ({page, request}) => {
  test.skip(process.env.HOSTED_TEST !== '1', 'requires the combined Render image');
  const token = process.env.API_TOKEN!;
  expect(token).toBeTruthy();
  await page.goto('/');
  await expect(page.getByRole('button', {name: /Test Lab/})).toHaveCount(0);
  expect((await request.get('/stats')).status()).toBe(401);
  await page.getByLabel('API token', {exact: true}).fill(token);
  await page.getByRole('button', {name: /Health/}).click();
  await expect(page.getByRole('heading', {name: 'Backend 0', exact: true})).toBeVisible();
  await expect(page.getByRole('heading', {name: 'Backend 1', exact: true})).toHaveCount(0);
  await expect(page.getByText('READY', {exact: true})).toBeVisible();
  await page.getByRole('button', {name: /Generate/}).first().click();
  const vehicle = `HOSTED-${Date.now()}`;
  await page.getByLabel('vehicle_id', {exact: true}).fill(vehicle);
  await page.getByRole('button', {name: 'Generate IDs →'}).click();
  await expect(page.getByRole('status')).toContainText('Committed');
  await page.getByRole('button', {name: /History/}).click();
  await page.getByRole('button', {name: 'Search history'}).click();
  await expect(page.locator('tbody tr')).toHaveCount(1);
  await page.getByRole('button', {name: /Paths/}).click();
  await page.getByRole('button', {name: 'Live map', exact: true}).click();
  await expect(page.getByText('Following ' + vehicle, {exact: true})).toBeVisible();
  await expect(page.getByText('Latest position', {exact: true})).toBeVisible();
  await page.screenshot({path: 'test-results/hosted-dashboard.png', fullPage: true});
});
