// render_screenshot.js (ESM)
import puppeteer from 'puppeteer';
import fs from 'fs';
import path from 'path';

(async () => {
  try {
    const htmlPath = `file://${process.cwd()}/docs/index.html`;
    const outPath = path.join(process.cwd(), 'docs', 'sunburst_screenshot.png');
    const browser = await puppeteer.launch({
      args: ['--no-sandbox', '--disable-setuid-sandbox']
    });
    const page = await browser.newPage();
    await page.setViewport({ width: 1400, height: 900 });

    await page.goto(htmlPath, { waitUntil: 'networkidle0' });

    // Wait for chart svg to appear - allow generous timeout
    await page.waitForSelector('#chart svg', { timeout: 15000 });

    // extra small wait to let D3 finish layout
    await page.waitForTimeout(500);

    const svgHandle = await page.$('#chart svg');
    if (!svgHandle) {
      console.error('SVG element not found');
      await browser.close();
      process.exit(2);
    }

    const boundingBox = await svgHandle.boundingBox();
    if (!boundingBox || boundingBox.width < 10 || boundingBox.height < 10) {
      console.log('SVG bbox suspicious, capturing full page as fallback');
      await page.screenshot({ path: outPath, fullPage: true });
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
    }

    console.log('Wrote screenshot:', outPath);
    await browser.close();
    process.exit(0);
  } catch (err) {
    console.error('Screenshot error:', err);
    process.exit(1);
  }
})();
