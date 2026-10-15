import {test,expect} from '@playwright/test';
test('generate, decode, retrieve history, and retry without duplicates',async({page})=>{
 await page.goto('/');
 await page.screenshot({path:'test-results/dashboard.png',fullPage:true});
 const vehicle=`E2E-${Date.now()}`;
 await page.getByLabel('vehicle_id',{exact:true}).fill(vehicle);
 await page.getByRole('button',{name:'Generate IDs →'}).click();
 await expect(page.getByRole('status')).toContainText('Committed');
 const first=await page.locator('.id-item').first().innerText();
 await page.getByRole('button',{name:'Generate IDs →'}).click();
 await expect(page.locator('.id-item').first()).toContainText(first.replace('↗','').trim());
 await page.locator('.id-item').first().click();
 await expect(page.locator('.details')).toContainText('timestamp');
 await page.getByRole('button',{name:/History/}).first().click();
 await page.getByRole('button',{name:'Search history'}).click();
 await expect(page.locator('tbody tr')).toHaveCount(1);
 await page.getByRole('button',{name:'Latest record'}).click();
 await expect(page.locator('pre')).toContainText(vehicle);
 await page.reload();
 await page.getByRole('button',{name:/History/}).first().click();
 await page.getByLabel('History vehicle').fill(vehicle);
 await page.getByRole('button',{name:'Search history'}).click();
 await expect(page.locator('tbody tr')).toHaveCount(1);
});

test('mobile layout and disconnected health remain usable',async({page})=>{
 await page.setViewportSize({width:390,height:844});
 await page.route('**/nodes/**',route=>route.abort());
 await page.goto('/');
 await page.getByRole('button',{name:/Health/}).first().click();
 await expect(page.getByLabel('API token',{exact:true})).toBeVisible();
 await expect(page.getByText('Metrics are unavailable, not zero.').first()).toBeVisible();
 await expect(page.locator('main')).toBeVisible();
 await page.screenshot({path:'test-results/mobile-health.png',fullPage:true});
});

test('test lab launches a bounded run and exposes its report',async({page})=>{
 test.skip(!process.env.TEST_TOKEN,'requires compose.test.yaml and TEST_TOKEN');
 await page.goto('/');
 await page.getByRole('button',{name:/Test Lab/}).first().click();
 await page.getByLabel('Test access token').fill(process.env.TEST_TOKEN!);
 await page.getByLabel('duration',{exact:true}).fill('2');
 await page.getByLabel('total ids',{exact:true}).fill('100');
 await page.getByRole('button',{name:'Start test →'}).click();
 await expect(page.locator('.badge').filter({hasText:'completed'})).toBeVisible({timeout:20000});
 await expect(page.locator('pre')).toContainText('"duplicate_ids": 0');
});
