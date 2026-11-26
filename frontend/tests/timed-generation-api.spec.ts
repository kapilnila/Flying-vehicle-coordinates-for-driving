import {expect, test} from '@playwright/test';

test('timed generation persists distinct records retrievable through History', async ({page, request}) => {
  test.skip(!process.env.TIMED_API_URL, 'requires an isolated temporary API');
  const base = process.env.TIMED_API_URL!;
  const vehicle = `TIMED-${Date.now()}`;
  await page.goto(base);
  await page.getByLabel('vehicle_id', {exact: true}).fill(vehicle);
  await page.getByLabel('captured_at_ms', {exact: true}).fill('123456');
  await page.getByRole('checkbox', {name: /Generate continuously/}).check();
  await page.getByLabel('Duration (seconds)').fill('3');
  await page.getByLabel('Interval (seconds)').fill('1');
  await page.getByRole('button', {name: 'Start timed generation'}).click();
  await expect(page.locator('.timed-generation [role="status"]')).toContainText('Completed', {timeout: 15000});
  const response = await request.get(`${base}/v1/vehicles/${vehicle}/records`);
  expect(response.ok()).toBe(true);
  const {items} = await response.json();
  expect(items.length).toBeGreaterThanOrEqual(2);
  expect(items.length).toBeLessThanOrEqual(3);
  expect(new Set(items.map((item: any) => item.id)).size).toBe(items.length);
  expect(new Set(items.map((item: any) => item.request_key)).size).toBe(items.length);
  for (const item of items) {
    expect(item).toMatchObject({vehicle_id: vehicle, captured_at_ms: 123456,
      latitude: 12.9716, longitude: 77.5946, altitude_m: 120.4, speed_mps: 17.8, altitude_reference: 'unknown'});
    const saved = await request.get(`${base}/v1/records/${item.id}`);
    expect(await saved.json()).toEqual(item);
  }
  await page.locator('nav').getByRole('button', {name: /History/}).click();
  await page.getByRole('button', {name: 'Search history'}).click();
  await expect(page.locator('tbody tr')).toHaveCount(items.length);
});
