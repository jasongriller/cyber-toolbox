import { expect, test, type Page } from '@playwright/test';

const CONFIG = {
  aiAvailable: false,
  aiReason: 'disabled-globally',
  maxUploadBytes: 209715200,
  allowedExtensions: ['.cklb', '.nessus', '.xml', '.zip'],
};

const SUMMARY = { files: 1, hosts: 2, findings: 5, cat1: 1, cat2: 2, cat3: 2 };

/** Mock the whole API surface. No GovCloud credentials in CI, by design. */
async function mockApi(
  page: Page,
  opts: { statuses: string[]; resultStatus?: number },
) {
  let poll = 0;

  await page.route('**/config', (r) =>
    r.fulfill({ json: CONFIG }));

  await page.route('**/uploads', (r) =>
    r.fulfill({
      status: 201,
      json: { jobId: 'j1', uploads: [{ filename: 'scan.xml', url: 'https://s3.test/put' }] },
    }));

  // The presigned PUT goes straight to S3, not through the API.
  await page.route('https://s3.test/**', (r) => r.fulfill({ status: 200, body: '' }));

  await page.route('**/jobs', (r) =>
    r.fulfill({ status: 202, json: { jobId: 'j1', ai: 'disabled-globally' } }));

  await page.route('**/jobs/j1', (r) => {
    const status = opts.statuses[Math.min(poll, opts.statuses.length - 1)];
    poll += 1;
    r.fulfill({
      json: {
        jobId: 'j1',
        status,
        progress: status === 'complete' ? 'Done — 5 findings exported.' : 'Parsing files…',
        warnings: ['Benchmark unmatched for 1 file'],
        ...(status === 'complete' ? { summary: SUMMARY } : {}),
      },
    });
  });

  await page.route('**/jobs/j1/result', (r) => {
    if (opts.resultStatus === 410) {
      return r.fulfill({ status: 410, json: { error: 'Report expired.' } });
    }
    return r.fulfill({ json: { url: 'https://s3.test/report.xlsx' } });
  });

  await page.route('**/jobs/j1/cancel', (r) =>
    r.fulfill({ json: { jobId: 'j1', status: 'cancelled' } }));
}

test('upload → poll → report ready', async ({ page }) => {
  await mockApi(page, { statuses: ['running', 'complete'] });
  await page.goto('/');

  await page.getByLabel('Scan Results files').setInputFiles({
    name: 'scan.xml',
    mimeType: 'text/xml',
    buffer: Buffer.from('<xml/>'),
  });

  await page.getByRole('button', { name: 'Process' }).click();

  await expect(page.getByRole('status')).toContainText('Report Ready', { timeout: 15_000 });
  // The Findings cell specifically. `getByText('5')` was a whole-page substring
  // match that passed by luck — it would have gone on passing had the findings
  // count rendered as 0, so long as a 5 appeared anywhere else on the page.
  await expect(page.locator('.summary-total dd')).toHaveText('5');
  // Warnings must survive onto the success card.
  await expect(page.getByText('Benchmark unmatched for 1 file')).toBeVisible();
});

test('an expired report says so instead of silently doing nothing', async ({ page }) => {
  // The retention window closed between the job finishing and the click. Without
  // a handler this rejection vanished into the console and the operator was left
  // clicking a dead button under a card that still read "Report Ready".
  await mockApi(page, { statuses: ['running', 'complete'], resultStatus: 410 });
  await page.goto('/');

  await page.getByLabel('Scan Results files').setInputFiles({
    name: 'scan.xml',
    mimeType: 'text/xml',
    buffer: Buffer.from('<xml/>'),
  });
  await page.getByRole('button', { name: 'Process' }).click();

  await expect(page.getByRole('status')).toContainText('Report Ready', { timeout: 15_000 });
  await page.getByRole('button', { name: 'Download Excel Report' }).click();

  await expect(page.getByRole('alert')).toContainText('expired');
});

test('AI toggle is disabled and says why', async ({ page }) => {
  await mockApi(page, { statuses: ['running'] });
  await page.goto('/');

  await expect(page.getByRole('checkbox', { name: /ai enrichment/i })).toBeDisabled();
  await expect(page.getByText(/no model is approved/i)).toBeVisible();
});

test('cancel returns to the upload form', async ({ page }) => {
  await mockApi(page, { statuses: ['running'] });
  await page.goto('/');

  await page.getByLabel('Scan Results files').setInputFiles({
    name: 'scan.xml',
    mimeType: 'text/xml',
    buffer: Buffer.from('<xml/>'),
  });
  await page.getByRole('button', { name: 'Process' }).click();
  await page.getByRole('button', { name: 'Cancel' }).click();

  await expect(page.getByRole('button', { name: 'Process' })).toBeVisible();
});

test('a report that was never downloaded asks before being discarded', async ({ page }) => {
  await mockApi(page, { statuses: ['complete'] });
  await page.goto('/');

  await page.getByLabel('Scan Results files').setInputFiles({
    name: 'scan.xml',
    mimeType: 'text/xml',
    buffer: Buffer.from('<xml/>'),
  });
  await page.getByRole('button', { name: 'Process' }).click();
  await expect(page.getByRole('status')).toContainText('Report Ready', { timeout: 15_000 });

  // First click asks instead of discarding.
  await page.getByRole('button', { name: /process another set/i }).click();
  await expect(page.getByText(/haven't downloaded/i)).toBeVisible();

  // Keep: the card stays intact.
  await page.getByRole('button', { name: /keep report/i }).click();
  await expect(page.getByRole('status')).toContainText('Report Ready');

  // Discard: through to a fresh upload form.
  await page.getByRole('button', { name: /process another set/i }).click();
  await page.getByRole('button', { name: /discard & start new/i }).click();
  await expect(page.getByRole('button', { name: 'Process' })).toBeVisible();
});

test('the toolbox bar links home and to the sibling tool', async ({ page }) => {
  await mockApi(page, { statuses: ['running'] });
  await page.goto('/');

  // Served from "/" here, so the runtime stage derivation degrades to "/";
  // deployed, the same links carry the API Gateway stage prefix.
  const nav = page.getByRole('navigation', { name: /cyber toolbox/i });
  await expect(nav.getByRole('link', { name: /cyber toolbox/i })).toHaveAttribute('href', '/');
  await expect(nav.getByRole('link', { name: /rmf migrator/i })).toHaveAttribute(
    'href',
    '/rmf/index.html',
  );
});

test('the whole flow is operable by keyboard alone', async ({ page }) => {
  await mockApi(page, { statuses: ['running', 'complete'] });
  await page.goto('/');

  // Set the file via the input (a real user uses the OS picker, which the
  // keyboard reaches through the Choose Files button — asserted below).
  await page.getByLabel('Scan Results files').setInputFiles({
    name: 'scan.xml',
    mimeType: 'text/xml',
    buffer: Buffer.from('<xml/>'),
  });

  // The toolbox bar's two links come first in tab order; the upload form is
  // still reachable right behind them.
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: /cyber toolbox/i })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('link', { name: /rmf migrator/i })).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.getByRole('button', { name: /choose files/i }).first()).toBeFocused();

  await page.getByRole('button', { name: 'Process' }).focus();
  await page.keyboard.press('Enter');

  await expect(page.getByRole('status')).toContainText('Report Ready', { timeout: 15_000 });
});
