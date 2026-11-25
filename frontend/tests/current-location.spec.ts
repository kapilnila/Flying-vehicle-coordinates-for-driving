import {expect, test} from '@playwright/test';

test.beforeEach(async ({page}) => {
  await page.route('**/v1/config', route => route.fulfill({json: {public_api: true}}));
  await page.route('**/nodes/**', route => route.fulfill({json: {status: 'ready'}}));
});

test('location fills coordinates on click and is saved only after Generate', async ({page, context}) => {
  await context.grantPermissions(['geolocation']);
  await context.setGeolocation({latitude: 51.5074, longitude: -0.1278, accuracy: 25});
  const submissions: Record<string, unknown>[] = [];
  await page.route('**/v1/ids', async route => {
    submissions.push(route.request().postDataJSON());
    await route.fulfill({status: 201, json: {ids: ['0123456789abcdef0123456789abcdef'], count: 1, worker_id: 0}});
  });
  await page.goto('/');
  await expect(page.getByLabel('latitude', {exact: true})).toHaveValue('12.9716');
  await page.getByRole('button', {name: 'Use my current location'}).click();
  await expect(page.getByLabel('latitude', {exact: true})).toHaveValue('51.5074');
  await expect(page.getByLabel('longitude', {exact: true})).toHaveValue('-0.1278');
  await expect(page.getByRole('status')).toContainText('±25 m');
  await expect(page.getByLabel('altitude_m', {exact: true})).toHaveValue('120.4');
  await expect(page.getByLabel('altitude_reference', {exact: true})).toHaveValue('unknown');
  expect(submissions).toHaveLength(0);
  await page.getByRole('button', {name: 'Generate IDs →'}).click();
  await expect(page.getByText('Committed to durable history.')).toBeVisible();
  expect(submissions[0]).toMatchObject({latitude: 51.5074, longitude: -0.1278});
  expect(submissions[0].captured_at_ms).toBeGreaterThan(0);
  await page.getByLabel('latitude', {exact: true}).fill('1');
  await expect(page.getByText(/Location filled/)).toHaveCount(0);
});

for (const [code, message] of [[1, 'permission was denied'], [2, 'could not determine'], [3, 'timed out']] as const) {
  test('location failure ' + code + ' preserves editable coordinates', async ({page}) => {
    await page.addInitScript(code => {
      navigator.geolocation.getCurrentPosition = (_success, failure) => failure?.({code} as GeolocationPositionError);
    }, code);
    await page.goto('/');
    await page.getByRole('button', {name: 'Use my current location'}).click();
    await expect(page.getByRole('alert')).toContainText(message);
    await expect(page.getByLabel('latitude', {exact: true})).toHaveValue('12.9716');
    await expect(page.getByRole('button', {name: 'Generate IDs →'})).toBeEnabled();
  });
}

test('late reading cannot overwrite manual edits or a cancelled request', async ({page}) => {
  await page.addInitScript(() => {
    navigator.geolocation.getCurrentPosition = success => {
      (window as any).resolveLocation = () => success({
        coords: {latitude: 10, longitude: 20, accuracy: 30}, timestamp: Date.now(),
      } as GeolocationPosition);
    };
  });
  await page.goto('/');
  await page.getByRole('button', {name: 'Use my current location'}).click();
  await expect(page.getByRole('button', {name: 'Generate IDs →'})).toBeDisabled();
  await page.getByLabel('latitude', {exact: true}).fill('5');
  await page.evaluate(() => (window as any).resolveLocation());
  await expect(page.getByLabel('latitude', {exact: true})).toHaveValue('5');
  await page.getByRole('button', {name: 'Use my current location'}).click();
  await page.getByRole('button', {name: 'Cancel location'}).click();
  await page.evaluate(() => (window as any).resolveLocation());
  await expect(page.getByLabel('latitude', {exact: true})).toHaveValue('5');
});

test('unsupported browsers show manual-entry guidance', async ({page}) => {
  await page.addInitScript(() => Object.defineProperty(navigator, 'geolocation', {value: undefined}));
  await page.goto('/');
  await page.getByRole('button', {name: 'Use my current location'}).click();
  await expect(page.getByRole('alert')).toContainText('Enter coordinates manually');
});
