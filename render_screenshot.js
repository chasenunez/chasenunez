// render_screenshot.js
// Usage: node render_screenshot.js
// This opens docs/sunburst.html with headless Chromium and screenshots the plotly div.

const puppeteer = require('puppeteer');

(async () => {
  try {
    const htmlPath = `file://${process.cwd()}/docs/sunburst.html`;
    const outPath = `${process.cwd()}/docs/sunburst_screenshot.png`;
    const browser = await puppeteer.launch({
      args: ['--no-sandbox', '--disable-setuid-sandbox']
    });
    const page = await browser.newPage();
    await page.setViewport({ width: 1400, height: 900 });
    // load the generated HTML; wait for network idle and the plot div
    await page.goto(htmlPath, { waitUntil: 'networkidle0' });
    await page.waitForSelector('.plotly-graph-div', { timeout: 8000 });
    const el = await page.$('.plotly-graph-div');
    if (!el) {
      console.error('ERROR: plotly element not found');
      await browser.close();
      process.exit(2);
    }
    // screenshot the plot div (not whole page)
    await el.screenshot({ path: outPath });
    console.log('Wrote screenshot:', outPath);
    await browser.close();
    process.exit(0);
  } catch (err) {
    console.error('Screenshot error:', err);
    process.exit(1);
  }
})();
