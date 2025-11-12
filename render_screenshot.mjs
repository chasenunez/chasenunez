// render_screenshot.mjs
import puppeteer from 'puppeteer';
import path from 'path';

function sleep(ms){ return new Promise(r=>setTimeout(r,ms)); }

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

    await page.goto(htmlPath, { waitUntil: 'networkidle0' });

    // diagnostics
    try {
      const dataSnippet = await page.evaluate(() => {
        const el = document.getElementById('flare-data');
        if (!el) return {exists:false};
        return {exists:true, len: el.textContent.length, head: el.textContent.slice(0,200)};
      });
      console.log("flare-data element:", dataSnippet);
      const pageLabel = await page.evaluate(() => document.getElementById('label')?.textContent || document.getElementById('barlabel')?.textContent || '');
      console.log("label text:", pageLabel);
    } catch(e){
      console.warn("diagnostic eval failed:", e);
    }

    // wait for SVG in #chart
    try {
      await page.waitForSelector('#chart svg', { timeout: 20000 });
      await sleep(600);
    } catch (e) {
      console.warn('waitForSelector #chart svg timed out - fallback to full-page capture', e);
    }

    const svgHandle = await page.$('#chart svg');
    if (!svgHandle) {
      console.warn('SVG element not found; capturing full page with transparent background');
      await page.screenshot({ path: outPath, fullPage: true, omitBackground: true });
      console.log('Wrote fallback full-page screenshot:', outPath);
      await browser.close();
      process.exit(0);
    }

    const bbox = await svgHandle.boundingBox();
    console.log('SVG bounding box:', bbox);

    if (!bbox || bbox.width < 8 || bbox.height < 8) {
      console.warn('SVG bbox suspicious; capturing full page fallback (transparent)');
      await page.screenshot({ path: outPath, fullPage: true, omitBackground: true });
      console.log('Wrote fallback full-page screenshot:', outPath);
    } else {
      await page.screenshot({
        path: outPath,
        clip: {
          x: Math.floor(bbox.x),
          y: Math.floor(bbox.y),
          width: Math.ceil(bbox.width),
          height: Math.ceil(bbox.height)
        },
        omitBackground: true
      });
      console.log('Wrote clipped screenshot with transparent background:', outPath);
    }

    await browser.close();
    process.exit(0);
  } catch (err) {
    console.error('Screenshot error:', err);
    process.exit(1);
  }
})();
