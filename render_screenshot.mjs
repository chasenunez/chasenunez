// render_screenshot.mjs
// ESM module, robust across puppeteer versions.
// Usage: node render_screenshot.mjs

import puppeteer from 'puppeteer';
import fs from 'fs';
import path from 'path';

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

(async () => {
  try {
    const htmlPath = `file://${process.cwd()}/docs/index.html`;
    const outPath = path.join(process.cwd(), 'docs', 'sunburst_screenshot.png');
    console.log('Opening', htmlPath);

    const browser = await puppeteer.launch({
      args: ['--no-sandbox', '--disable-setuid-sandbox']
    });
    const page = await browser.newPage();
    await page.setViewport({ width: 1400, height: 900 });

    // go to the local file and wait for network idle to allow resources
    await page.goto(htmlPath, { waitUntil: 'networkidle0' });

    // debug: print inline JSON existence + snippet and page label text
    try {
      const dataSnippet = await page.evaluate(() => {
        const el = document.getElementById('flare-data');
        if (!el) return {exists: false};
        return {exists: true, len: el.textContent.length, head: el.textContent.slice(0,200)};
      });
      console.log("flare-data element:", dataSnippet);

      const pageLabel = await page.evaluate(() => document.getElementById('label')?.textContent || '');
      console.log("label text:", pageLabel);
    } catch (e) {
      console.log('Eval diagnostics failed:', e);
    }

    // Wait for the chart svg to appear. Larger timeout for slow renders.
    try {
      await page.waitForSelector('#chart svg', { timeout: 20000 });
      // small sleep to allow layout to finish
      await sleep(600);
    } catch (e) {
      console.warn('waitForSelector #chart svg timed out - will attempt fallback capture', e);
    }

    // Attempt to find the svg and capture its bounding box
    const svgHandle = await page.$('#chart svg');
    if (!svgHandle) {
      console.warn('SVG element not found; doing full-page screenshot fallback');
      await page.screenshot({ path: outPath, fullPage: true });
      console.log('Wrote full-page fallback screenshot:', outPath);
      await browser.close();
      process.exit(0);
    }

    const boundingBox = await svgHandle.boundingBox();
    console.log('SVG boundingBox:', boundingBox);

    if (!boundingBox || boundingBox.width < 8 || boundingBox.height < 8) {
      console.warn('SVG bbox suspicious (small or null) - capturing full page fallback');
      await page.screenshot({ path: outPath, fullPage: true });
      console.log('Wrote full-page fallback screenshot:', outPath);
    } else {
      await page.screenshot({
        path: outPath,
        clip: {
          x: Math.floor(boundingBox.x),
          y: Math.floor(boundingBox.y),
          width: Math.ceil(boundingBox.width),
          height: Math.ceil(boundingBox.height)
        }
      });
      console.log('Wrote clipped screenshot:', outPath);
    }

    await browser.close();
    process.exit(0);
  } catch (err) {
    console.error('Screenshot error:', err);
    process.exit(1);
  }
})();
