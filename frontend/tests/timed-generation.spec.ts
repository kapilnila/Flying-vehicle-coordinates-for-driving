import {expect, test, type Page} from '@playwright/test';

const response = {ids: ['0123456789abcdef0123456789abcdef'], count: 1, worker_id: 0};
async function enable(page: Page) {
  await page.goto('/');
  await page.clock.install({time: new Date('2026-11-26T00:00:00Z')});
  await page.clock.pauseAt(new Date('2026-11-26T00:01:00Z'));
  await page.getByRole('checkbox', {name: /Generate continuously/}).check();
}
const progress = (page: Page) => page.locator('.timed-generation [role="status"]');

test.beforeEach(async ({page}) => {
  await page.route('**/v1/config', route => route.fulfill({json: {public_api: true}}));
  await page.route('**/nodes/**', route => route.fulfill({json: {status: 'ready'}}));
});

test('optional one-minute run saves one ID every five seconds with unique keys and stops', async ({page}) => {
  const sent: {body: any; key: string}[] = [];
  await page.route('**/v1/ids', async route => {
    sent.push({body: route.request().postDataJSON(), key: route.request().headers()['idempotency-key']});
    await route.fulfill({status: 201, json: response});
  });
  await page.goto('/');
  await expect(page.getByRole('checkbox', {name: /Generate continuously/})).not.toBeChecked();
  await expect(page.getByRole('button', {name: 'Start timed generation'})).toHaveCount(0);
  expect(sent).toHaveLength(0);
  await enable(page);
  await expect(page.getByLabel('Duration (seconds)')).toHaveValue('60');
  await page.getByLabel('count', {exact: true}).fill('20');
  await page.getByRole('button', {name: 'Start timed generation'}).click();
  await expect(progress(page)).toContainText('1 IDs saved');
  await expect(page.getByLabel('latitude', {exact: true})).toBeDisabled();
  await expect(page.getByRole('button', {name: 'Generate IDs →'})).toBeDisabled();
  for (let i = 2; i <= 12; i++) {
    await page.clock.runFor(5000);
    await expect(progress(page)).toContainText(`${i} IDs saved`);
  }
  await page.clock.runFor(5000);
  await expect(progress(page)).toContainText('Completed · 12 IDs saved');
  await page.clock.runFor(60000);
  expect(sent).toHaveLength(12);
  expect(new Set(sent.map(item => item.key)).size).toBe(12);
  expect(sent.every(item => item.body.count === 1 && item.body.latitude === 12.9716)).toBe(true);
  await expect(page.getByRole('button', {name: 'Generate IDs →'})).toBeEnabled();
});

test('Stop allows one pending request to finish without overlapping or scheduling more', async ({page}) => {
  let release!: () => void;
  const held = new Promise<void>(resolve => {release = resolve;});
  let requests = 0;
  await page.route('**/v1/ids', async route => {
    requests++;
    await held;
    await route.fulfill({status: 201, json: response});
  });
  await enable(page);
  await page.getByRole('button', {name: 'Start timed generation'}).click();
  await expect.poll(() => requests).toBe(1);
  await page.clock.runFor(10000);
  expect(requests).toBe(1);
  await page.getByRole('button', {name: 'Stop generation'}).click();
  await expect(progress(page)).toContainText('waiting for the current reading');
  release();
  await expect(progress(page)).toContainText('Stopped · 1 IDs saved');
  await page.clock.runFor(60000);
  expect(requests).toBe(1);
});

test('fresh locations and timestamps are submitted while reviewed altitude and speed stay unchanged', async ({page}) => {
  await page.addInitScript(() => {
    let n = 0;
    navigator.geolocation.getCurrentPosition = success => {
      n++;
      success({coords: {latitude: 10 + n / 100, longitude: 20, altitude: 999, speed: 999}, timestamp: Date.now()} as GeolocationPosition);
    };
  });
  const bodies: any[] = [];
  await page.route('**/v1/ids', async route => {
    bodies.push(route.request().postDataJSON());
    await route.fulfill({json: response});
  });
  await enable(page);
  await page.getByLabel('Coordinate source').selectOption('device');
  await page.getByRole('button', {name: 'Start timed generation'}).click();
  await expect(progress(page)).toContainText('1 IDs saved');
  await page.clock.runFor(5000);
  await expect(progress(page)).toContainText('2 IDs saved');
  expect(bodies[0]).toMatchObject({latitude: 10.01, altitude_m: 120.4, speed_mps: 17.8, altitude_reference: 'unknown'});
  expect(bodies[1].latitude).toBe(10.02);
  expect(bodies[1].captured_at_ms - bodies[0].captured_at_ms).toBe(5000);
  await page.getByRole('button', {name: 'Stop generation'}).click();
});

test('a late location after the deadline creates no record', async ({page}) => {
  await page.addInitScript(() => {
    navigator.geolocation.getCurrentPosition = success => {
      (window as any).resolveTimedLocation = () => success({coords: {latitude: 10, longitude: 20}, timestamp: Date.now()} as GeolocationPosition);
    };
  });
  let requests = 0;
  await page.route('**/v1/ids', route => {requests++; return route.fulfill({json: response});});
  await enable(page);
  await page.getByLabel('Coordinate source').selectOption('device');
  await page.getByLabel('Duration (seconds)').fill('1');
  await page.getByRole('button', {name: 'Start timed generation'}).click();
  await page.clock.runFor(1000);
  await page.evaluate(() => (window as any).resolveTimedLocation());
  await expect(progress(page)).toContainText('Completed · 0 IDs saved');
  expect(requests).toBe(0);
});

test('navigation stops generation and returning does not restart it', async ({page}) => {
  let requests = 0;
  await page.route('**/v1/ids', route => {requests++; return route.fulfill({json: response});});
  await enable(page);
  await page.getByRole('button', {name: 'Start timed generation'}).click();
  await expect(progress(page)).toContainText('1 IDs saved');
  await page.locator('nav').getByRole('button', {name: /History/}).click();
  await page.clock.runFor(60000);
  await page.locator('nav').getByRole('button', {name: /Generate/}).click();
  expect(requests).toBe(1);
  await expect(page.getByRole('checkbox', {name: /Generate continuously/})).not.toBeChecked();
  await expect(page.getByRole('button', {name: 'Generate IDs →'})).toBeEnabled();
});

test('failed submissions stop instead of automatically retrying', async ({page}) => {
  let requests = 0;
  await page.route('**/v1/ids', route => {requests++; return route.fulfill({status: 503, json: {error: {message: 'Service unavailable'}}});});
  await enable(page);
  await page.getByRole('button', {name: 'Start timed generation'}).click();
  await expect(page.getByRole('alert')).toContainText('No automatic retry');
  await expect(progress(page)).toContainText('Stopped after an error · 0 IDs saved');
  await page.clock.runFor(60000);
  expect(requests).toBe(1);
});

test('bounds and vehicle validation prevent starting; mobile controls fit the page', async ({page}) => {
  await page.setViewportSize({width: 390, height: 844});
  await enable(page);
  await page.getByLabel('Duration (seconds)').fill('0');
  await page.getByRole('button', {name: 'Start timed generation'}).click();
  await expect(page.getByRole('alert')).toContainText('1–3600');
  await page.getByRole('button', {name: '1 minute', exact: true}).click();
  await page.getByLabel('vehicle_id', {exact: true}).fill('');
  await page.getByRole('button', {name: 'Start timed generation'}).click();
  await expect(page.getByRole('alert')).toContainText('Enter a vehicle ID');
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({path: 'test-results/timed-generation-mobile.png', fullPage: true});
});
