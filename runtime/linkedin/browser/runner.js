#!/usr/bin/env node
/* Account-aware LinkedIn browser adapter. It never reads or prints browser secrets. */

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

function arg(name, fallback = null) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : fallback;
}

function loadJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

function cleanUrl(value) {
  if (!value) return null;
  try {
    const url = new URL(value);
    url.search = '';
    url.hash = '';
    return url.toString();
  } catch {
    return value;
  }
}

function sha(value) {
  return crypto.createHash('sha256').update(String(value || ''), 'utf8').digest('hex');
}

function slugFromProfile(value) {
  const match = String(value || '').match(/linkedin\.com\/in\/([^/?#]+)/i);
  return match ? match[1].toLowerCase() : null;
}

async function detectIdentity(ctx) {
  const page = await ctx.newPage();
  try {
    await page.goto('https://www.linkedin.com/in/me/', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3200);
    const profileUrl = cleanUrl(page.url());
    const slug = slugFromProfile(profileUrl);
    if (!slug || /\/login|\/checkpoint/i.test(profileUrl || '')) {
      throw new Error('linkedin identity unavailable');
    }
    const displayName = await page.locator('meta[property="og:title"]').getAttribute('content').catch(() => null)
      || await page.locator('h1').first().innerText().catch(() => null)
      || (await page.title()).split('|')[0].trim()
      || slug;
    return { slug, profileUrl, displayName };
  } finally {
    await page.close().catch(() => {});
  }
}

function assertIdentity(actual, expected) {
  const expectedSlug = String(expected || '').toLowerCase();
  if (!expectedSlug || actual.slug !== expectedSlug) {
    throw new Error(`linkedin account mismatch: expected ${expectedSlug || 'unset'}, detected ${actual.slug}`);
  }
}

async function setRecent(page) {
  await page.goto('https://www.linkedin.com/feed/', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3800);
  const sort = page.getByRole('button', { name: /sort by/i }).first();
  if (!(await sort.count())) throw new Error('linkedin feed sort control unavailable');
  const label = async () => [
    await sort.innerText().catch(() => ''),
    await sort.getAttribute('aria-label').catch(() => ''),
    await sort.getAttribute('title').catch(() => ''),
  ].filter(Boolean).join(' ').trim();
  const before = await label();
  if (!/recent/i.test(before)) {
    await sort.click();
    await page.waitForTimeout(600);
    const recent = page.getByRole('menuitem', { name: /^recent$/i }).or(page.getByText(/^Recent$/i)).first();
    if (!(await recent.count())) throw new Error('linkedin Recent menu item unavailable');
    await recent.click();
    await page.waitForTimeout(1800);
  }
  const after = await label();
  if (!/recent/i.test(after)) throw new Error(`linkedin Recent sort not verified (${after || 'blank'})`);
  return after;
}

async function scanFeed(ctx, config, outputDir) {
  const page = await ctx.newPage();
  try {
    const sortLabel = await setRecent(page);
    const maxScrolls = Number(arg('--scan-scrolls', config.public_replies?.scan_scrolls || 16));
    const candidateLimit = Number(config.public_replies?.scan_limit || 60);
    for (let index = 0; index < maxScrolls; index += 1) {
      await page.mouse.wheel(0, 2400);
      await page.waitForTimeout(750);
    }
    const feedDiagnostics = await page.evaluate(() => ({
      url: location.href,
      title: document.title,
      dataUrn: document.querySelectorAll('[data-urn]').length,
      dataId: document.querySelectorAll('[data-id]').length,
      feedUpdates: document.querySelectorAll('.feed-shared-update-v2').length,
      fullUpdates: document.querySelectorAll('[data-view-name="feed-full-update"]').length,
      articles: document.querySelectorAll('article').length,
      mainTextLength: (document.querySelector('main')?.innerText || '').length,
    }));
    fs.mkdirSync(outputDir, { recursive: true });
    await page.screenshot({ path: path.join(outputDir, 'feed-viewport.png'), fullPage: false }).catch(() => {});
    let candidates = await page.evaluate((limit) => {
      const nodes = [...document.querySelectorAll('.feed-shared-update-v2, [data-view-name="feed-full-update"], [data-urn], main [data-id]')].filter((node) =>
        /urn:li:(activity|ugcPost|share):/.test(node.getAttribute('data-urn') || node.getAttribute('data-id') || '')
      );
      const seen = new Set();
      const rows = [];
      for (const node of nodes) {
        const urn = node.getAttribute('data-urn') || node.getAttribute('data-id');
        if (!urn || seen.has(urn)) continue;
        seen.add(urn);
        const fullText = (node.innerText || '').replace(/\n{3,}/g, '\n\n').trim();
        if (fullText.length < 40) continue;
        const profile = [...node.querySelectorAll('a[href*="/in/"]')].find((a) => (a.innerText || '').trim());
        const authorProfileUrl = profile ? profile.href.split('?')[0] : null;
        const author = profile ? (profile.innerText || '').replace(/\s+/g, ' ').trim() : '';
        const textNode = node.querySelector('.update-components-text, [class*="update-components-text"], .feed-shared-update-v2__description');
        const body = ((textNode && textNode.innerText) || fullText).trim().slice(0, 5000);
        const postLink = [...node.querySelectorAll('a[href]')].find((a) => /\/feed\/update\/|\/posts\//.test(a.href || ''));
        const canonicalUrl = postLink ? postLink.href.split('?')[0] : `https://www.linkedin.com/feed/update/${urn}/`;
        const social = (node.querySelector('[class*="social-context"]')?.innerText || '').trim();
        const firstLine = fullText.split('\n').map((s) => s.trim()).find(Boolean) || '';
        rows.push({
          urn,
          canonicalUrl,
          author,
          authorProfileUrl,
          body,
          promoted: /\bpromoted\b/i.test(fullText),
          suggested: /\bsuggested\b/i.test(firstLine),
          repost: /reposted this|reposted/i.test(social || firstLine),
          commented: /commented on|commented$/i.test(social || firstLine),
          hiring: /\bwe(?:'re| are) hiring\b|\bi(?:'m| am) hiring\b|\bhiring for\b/i.test(body),
          hasMedia: Boolean(node.querySelector('img, video, [class*="image"], [class*="video"]')),
          visibleText: fullText.slice(0, 1200),
        });
        if (rows.length >= limit) break;
      }
      return rows;
    }, candidateLimit);
    if (!candidates.length) {
      candidates = await page.evaluate((limit) => {
        const buttons = [...document.querySelectorAll('button, [role="button"]')].filter((button) =>
          /^comment$/i.test((button.innerText || button.getAttribute('aria-label') || '').trim())
        );
        const cards = [];
        const used = new Set();
        for (const button of buttons) {
          let node = button.parentElement;
          let card = null;
          for (let depth = 0; depth < 14 && node; depth += 1, node = node.parentElement) {
            const labels = [...node.querySelectorAll('button, [role="button"]')].map((item) =>
              (item.innerText || item.getAttribute('aria-label') || '').trim().toLowerCase()
            );
            if (labels.includes('comment') && labels.includes('repost')
                && node.querySelector('a[href*="/in/"]')) {
              card = node;
              break;
            }
          }
          if (!card || used.has(card)) continue;
          used.add(card);
          const fullText = (card.innerText || '').replace(/\n{3,}/g, '\n\n').trim();
          if (fullText.length < 40) continue;
          const profile = [...card.querySelectorAll('a[href*="/in/"]')].find((a) => (a.innerText || '').trim());
          const authorProfileUrl = profile ? profile.href.split('?')[0] : null;
          const author = profile ? (profile.innerText || '').replace(/\s+/g, ' ').trim() : '';
          const postLink = [...card.querySelectorAll('a[href]')].find((a) => /\/feed\/update\/|\/posts\//.test(a.href || ''));
          const canonicalUrl = postLink ? postLink.href.split('?')[0] : null;
          const urnMatch = String(canonicalUrl || '').match(/urn:li:(?:activity|ugcPost|share):\d+|activity-\d+/i);
          const urn = urnMatch ? urnMatch[0] : canonicalUrl;
          if (!urn || !authorProfileUrl) continue;
          const marker = `candidate-${cards.length}`;
          card.setAttribute('data-codex-linkedin-candidate', marker);
          const firstLine = fullText.split('\n').map((s) => s.trim()).find(Boolean) || '';
          const contentAnchor = fullText.split('\n').map((s) => s.replace(/\s+/g, ' ').trim())
            .find((line) => line.length >= 30 && !/^(like|comment|repost|send|follow|promoted)$/i.test(line)) || null;
          cards.push({
            urn,
            canonicalUrl,
            author,
            authorProfileUrl,
            body: fullText.slice(0, 5000),
            promoted: /\bpromoted\b/i.test(fullText),
            suggested: /\bsuggested\b/i.test(firstLine),
            repost: /reposted this|reposted/i.test(firstLine),
            commented: /commented on|commented$/i.test(firstLine),
            hiring: /\bwe(?:'re| are) hiring\b|\bi(?:'m| am) hiring\b|\bhiring for\b/i.test(fullText),
            hasMedia: Boolean(card.querySelector('img, video')),
            visibleText: fullText.slice(0, 1200),
            contentAnchor,
            domMarker: marker,
          });
          if (cards.length >= limit) break;
        }
        return cards;
      }, candidateLimit);
    }
    fs.mkdirSync(outputDir, { recursive: true });
    for (const row of candidates.slice(0, 30)) {
      let locator = page.locator(`[data-urn="${row.urn.replace(/"/g, '\\"')}"]`).first();
      if (!(await locator.count()) && row.domMarker) {
        locator = page.locator(`[data-codex-linkedin-candidate="${row.domMarker}"]`).first();
      }
      const screenshot = path.join(outputDir, `post-${sha(row.urn).slice(0, 16)}.png`);
      if (await locator.count()) {
        await locator.screenshot({ path: screenshot }).catch(() => {});
        if (fs.existsSync(screenshot)) row.screenshot = screenshot;
      }
      row.bodySha256 = sha(row.body);
      row.authorSlug = slugFromProfile(row.authorProfileUrl);
    }
    return { sortVerified: true, sortLabel, diagnostics: feedDiagnostics, candidates };
  } finally {
    await page.close().catch(() => {});
  }
}

async function parseActiveThread(page, row) {
  await page.waitForTimeout(1300);
  const threadUrl = cleanUrl(page.url());
  const data = await page.evaluate(() => {
    const items = [...document.querySelectorAll('div.msg-s-event-listitem[data-event-urn]')];
    const messages = items.map((item) => {
      const heading = (item.querySelector('.msg-s-event-listitem--group-a11y-heading')?.innerText || '').trim();
      const body = (item.querySelector('.msg-s-event-listitem__body')?.innerText
        || item.querySelector('.msg-s-event-listitem__message-bubble')?.innerText
        || '').trim();
      const time = item.querySelector('time');
      const profile = item.querySelector('a[href*="/in/"]');
      return {
        eventUrn: item.getAttribute('data-event-urn'),
        direction: /msg-s-event-listitem--self/.test(item.className) ? 'outbound' : 'inbound',
        senderHeading: heading,
        body,
        datetime: time ? time.getAttribute('datetime') : null,
        profileUrl: profile ? profile.href.split('?')[0] : null,
      };
    }).filter((message) => message.eventUrn && message.body);
    const profileUrls = [...new Set(messages.filter((message) => message.direction === 'inbound').map((message) => message.profileUrl).filter(Boolean))];
    const title = (document.querySelector('.msg-title-bar__title-bar-title')?.innerText || '').replace(/\s+/g, ' ').trim();
    return { messages, profileUrls, title };
  });
  for (const message of data.messages) message.bodySha256 = sha(message.body);
  const inbound = data.messages.filter((message) => message.direction === 'inbound');
  return {
    participant: row.participant,
    preview: row.preview,
    unreadCount: row.unreadCount,
    rowTimestamp: row.rowTimestamp,
    inMail: row.inMail,
    sponsored: row.sponsored,
    threadUrl,
    threadId: (threadUrl.match(/\/messaging\/thread\/([^/?#]+)/) || [null, null])[1],
    title: data.title,
    profileUrls: data.profileUrls,
    ambiguous: data.profileUrls.length !== 1 || /,|\band\s+\d+\s+other/i.test(data.title || row.participant || ''),
    messages: data.messages.slice(-30),
    replyTarget: inbound.length ? inbound[inbound.length - 1] : null,
  };
}

async function scanUnreadDms(ctx, config) {
  const page = await ctx.newPage();
  const threads = [];
  const seen = new Set();
  try {
    await page.goto('https://www.linkedin.com/messaging/', { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(3800);
    const unread = page.getByRole('button', { name: /^Unread$/i }).first();
    if (!(await unread.count())) throw new Error('linkedin Unread filter unavailable');
    await unread.click();
    await page.waitForTimeout(1600);
    const limit = Number(config.dm_replies?.scan_limit || 25);
    for (let index = 0; index < limit; index += 1) {
      const rows = page.locator('li.msg-conversation-listitem');
      const count = await rows.count();
      let selected = null;
      let selectedIndex = -1;
      for (let rowIndex = 0; rowIndex < count; rowIndex += 1) {
        const row = rows.nth(rowIndex);
        const parsed = await row.evaluate((item) => {
          const text = (item.innerText || '').replace(/\s+/g, ' ').trim();
          const participant = (item.querySelector('.msg-conversation-listitem__participant-names')?.innerText || '').trim();
          const preview = (item.querySelector('.msg-conversation-card__message-snippet-body, [class*="message-snippet"]')?.innerText || '').replace(/\s+/g, ' ').trim();
          const rowTimestamp = (item.querySelector('time')?.innerText || '').trim();
          const unreadMatch = text.match(/(\d+)\s+new notifications?/i);
          return {
            participant,
            preview,
            rowTimestamp,
            unreadCount: unreadMatch ? Number(unreadMatch[1]) : (/new notification/i.test(text) ? 1 : 0),
            inMail: /\bInMail\b/i.test(text),
            sponsored: /\bSponsored\b/i.test(text),
            key: `${participant}|${rowTimestamp}|${preview}`,
          };
        });
        if (!seen.has(parsed.key)) {
          selected = parsed;
          selectedIndex = rowIndex;
          break;
        }
      }
      if (!selected || selectedIndex < 0) break;
      seen.add(selected.key);
      const row = rows.nth(selectedIndex);
      const link = row.locator('.msg-conversation-listitem__link').first();
      if (!(await link.count())) {
        threads.push({ ...selected, error: 'conversation row not clickable' });
        continue;
      }
      await link.click();
      threads.push(await parseActiveThread(page, selected));
    }
    return { unreadFilterVerified: /filter=unread/i.test(page.url()), threads };
  } finally {
    await page.close().catch(() => {});
  }
}

async function submitComment(page, action, identity) {
  await page.goto(action.canonicalUrl, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(3500);
  let card = page.locator(`[data-urn="${action.urn.replace(/"/g, '\\"')}"]`).first();
  if (!(await card.count())) card = page.locator('[data-urn*="urn:li:"]').first();
  if (!(await card.count())) {
    const marked = await page.evaluate((authorSlug) => {
      const controls = [...document.querySelectorAll('button, [role="button"]')].filter((button) =>
        /^comment$/i.test((button.innerText || button.getAttribute('aria-label') || '').trim())
      );
      for (const control of controls) {
        let node = control.parentElement;
        for (let depth = 0; depth < 14 && node; depth += 1, node = node.parentElement) {
          const labels = [...node.querySelectorAll('button, [role="button"]')].map((item) =>
            (item.innerText || item.getAttribute('aria-label') || '').trim().toLowerCase()
          );
          const ownAuthor = [...node.querySelectorAll('a[href*="/in/"]')].some((link) =>
            (link.href || '').toLowerCase().includes(`/in/${authorSlug}`)
          );
          if (labels.includes('comment') && labels.includes('repost') && ownAuthor) {
            node.setAttribute('data-codex-linkedin-target', 'true');
            return true;
          }
        }
      }
      return false;
    }, action.authorSlug);
    if (marked) card = page.locator('[data-codex-linkedin-target="true"]').first();
  }
  if (!(await card.count())) return { actionId: action.actionId, status: 'failed', reason: 'post unavailable' };
  const observed = await card.evaluate((node) => {
    const profile = [...node.querySelectorAll('a[href*="/in/"]')].find((a) => (a.innerText || '').trim());
    const textNode = node.querySelector('.update-components-text, [class*="update-components-text"], .feed-shared-update-v2__description');
    return {
      authorProfileUrl: profile ? profile.href.split('?')[0] : null,
      body: ((textNode && textNode.innerText) || node.innerText || '').trim().slice(0, 5000),
    };
  });
  if (action.authorSlug && slugFromProfile(observed.authorProfileUrl) !== action.authorSlug) {
    return { actionId: action.actionId, status: 'failed', reason: 'post author mismatch' };
  }
  if (action.contentAnchor && !observed.body.replace(/\s+/g, ' ').includes(action.contentAnchor)) {
    return { actionId: action.actionId, status: 'failed', reason: 'post content changed' };
  }
  if (!action.contentAnchor && action.bodySha256 && sha(observed.body) !== action.bodySha256) {
    return { actionId: action.actionId, status: 'failed', reason: 'post content changed' };
  }
  const button = card.getByRole('button', { name: /^comment$/i }).first();
  if (!(await button.count())) return { actionId: action.actionId, status: 'failed', reason: 'comment unavailable' };
  await button.click();
  await page.waitForTimeout(700);
  const editor = card.locator('div.ql-editor[contenteditable="true"], [role="textbox"][contenteditable="true"]').last();
  await editor.waitFor({ state: 'visible', timeout: 12000 });
  await editor.click();
  await editor.type(action.text, { delay: 24 });
  const submitted = await editor.evaluate((el) => {
    let node = el.parentElement;
    for (let depth = 0; depth < 16 && node; depth += 1) {
      const target = [...node.querySelectorAll('button')].find((button) =>
        /^(comment|post)$/i.test((button.innerText || '').trim()) && !button.disabled
      );
      if (target) {
        target.click();
        return (target.innerText || '').trim();
      }
      node = node.parentElement;
    }
    return null;
  });
  if (!submitted) return { actionId: action.actionId, status: 'failed', reason: 'submit unavailable' };
  await page.waitForTimeout(3200);
  const confirmation = await card.evaluate(({ text, slug }) => {
    const matches = [...document.querySelectorAll('article, [class*="comments-comment-item"], [data-id]')].filter((node) =>
      (node.innerText || '').includes(text)
    );
    for (const node of matches) {
      const own = [...node.querySelectorAll('a[href*="/in/"]')].some((a) => (a.href || '').toLowerCase().includes(`/in/${slug}`));
      if (!own) continue;
      const ref = node.getAttribute('data-urn') || node.getAttribute('data-id');
      return { visible: true, platformReference: ref || `visible:${slug}` };
    }
    const exactText = [...document.querySelectorAll('*')].filter((node) =>
      node.children.length === 0 && (node.textContent || '').trim() === text
    );
    for (const textNode of exactText) {
      let node = textNode.parentElement;
      for (let depth = 0; depth < 12 && node; depth += 1, node = node.parentElement) {
        const own = [...node.querySelectorAll('a[href*="/in/"]')].some((a) =>
          (a.href || '').toLowerCase().includes(`/in/${slug}`)
        );
        if (own) return { visible: true, platformReference: node.getAttribute('data-urn') || node.getAttribute('data-id') || `visible:${slug}` };
      }
    }
    return { visible: false, platformReference: null };
  }, { text: action.text, slug: identity.slug });
  if (!confirmation.visible) {
    return { actionId: action.actionId, status: 'uncertain', reason: 'submitted but exact account comment not visible' };
  }
  return {
    actionId: action.actionId,
    status: 'confirmed',
    platformReference: `${action.urn}|${confirmation.platformReference}|${sha(action.text).slice(0, 16)}`,
    confirmationMethod: 'exact_text_and_owner_visible_under_target_post',
  };
}

async function submitDmReply(page, action, identity) {
  await page.goto(action.threadUrl, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(2800);
  const target = page.locator(`div.msg-s-event-listitem[data-event-urn="${action.inboundEventUrn.replace(/"/g, '\\"')}"]`).first();
  if (!(await target.count())) return { actionId: action.actionId, status: 'failed', reason: 'inbound message unavailable' };
  const observed = await target.evaluate((item) => ({
    direction: /msg-s-event-listitem--self/.test(item.className) ? 'outbound' : 'inbound',
    body: (item.querySelector('.msg-s-event-listitem__body')?.innerText || item.querySelector('.msg-s-event-listitem__message-bubble')?.innerText || '').trim(),
  }));
  if (observed.direction !== 'inbound' || sha(observed.body) !== action.inboundBodySha256) {
    return { actionId: action.actionId, status: 'failed', reason: 'inbound message mismatch' };
  }
  const hasLaterSelf = await target.evaluate((item) => {
    let next = item.nextElementSibling;
    while (next) {
      if (next.matches?.('div.msg-s-event-listitem[data-event-urn]') && /msg-s-event-listitem--self/.test(next.className)) return true;
      next = next.nextElementSibling;
    }
    return false;
  });
  if (hasLaterSelf) return { actionId: action.actionId, status: 'failed', reason: 'conversation already has a later outbound message' };
  const editor = page.locator('div.msg-form__contenteditable[contenteditable="true"], div[role="textbox"][contenteditable="true"]').last();
  await editor.waitFor({ state: 'visible', timeout: 12000 });
  await editor.click();
  await editor.type(action.text, { delay: 24 });
  const send = page.getByRole('button', { name: /^send$/i }).last();
  if (!(await send.count()) || !(await send.isEnabled())) {
    return { actionId: action.actionId, status: 'failed', reason: 'send button unavailable' };
  }
  await send.click();
  await page.waitForTimeout(2500);
  const confirmed = await page.locator('div.msg-s-event-listitem.msg-s-event-listitem--self[data-event-urn]').evaluateAll((items, text) => {
    for (const item of items.slice().reverse()) {
      const body = (item.querySelector('.msg-s-event-listitem__body')?.innerText || item.querySelector('.msg-s-event-listitem__message-bubble')?.innerText || '').trim();
      if (body === text) return { eventUrn: item.getAttribute('data-event-urn') };
    }
    return null;
  }, action.text);
  if (!confirmed) return { actionId: action.actionId, status: 'uncertain', reason: 'submitted but exact outbound message not visible' };
  return {
    actionId: action.actionId,
    status: 'confirmed',
    platformReference: confirmed.eventUrn,
    confirmationMethod: 'exact_text_visible_as_self_in_expected_thread',
  };
}

async function main() {
  const command = process.argv[2];
  const configPath = path.resolve(arg('--config'));
  const outputPath = path.resolve(arg('--output'));
  const config = loadJson(configPath);
  const adapterPath = path.resolve(config.browser.adapter_path);
  const { openBrowser } = require(adapterPath);
  const ctx = await openBrowser({ headless: true });
  const result = { command, startedAt: new Date().toISOString() };
  try {
    const identity = await detectIdentity(ctx);
    assertIdentity(identity, config.identity.linkedin_slug);
    result.identity = identity;
    if (command === 'scan') {
      const evidenceDir = path.resolve(config.local_root, 'evidence', 'raw', arg('--run-id', `run-${Date.now()}`));
      result.feed = await scanFeed(ctx, config, evidenceDir);
      result.dms = await scanUnreadDms(ctx, config);
    } else if (command === 'scan-feed') {
      const evidenceDir = path.resolve(config.local_root, 'evidence', 'raw', arg('--run-id', `run-${Date.now()}`));
      result.feed = await scanFeed(ctx, config, evidenceDir);
    } else if (command === 'apply') {
      const input = loadJson(path.resolve(arg('--input')));
      result.results = [];
      for (const action of input.actions || []) {
        const current = await detectIdentity(ctx);
        assertIdentity(current, config.identity.linkedin_slug);
        const page = await ctx.newPage();
        try {
          const item = action.kind === 'casual_comment'
            ? await submitComment(page, action, current)
            : action.kind === 'dm_reply'
              ? await submitDmReply(page, action, current)
              : { actionId: action.actionId, status: 'failed', reason: 'unsupported action kind' };
          result.results.push(item);
        } catch (error) {
          result.results.push({ actionId: action.actionId, status: 'failed', reason: error.message });
        } finally {
          await page.close().catch(() => {});
        }
      }
    } else if (command !== 'identity') {
      throw new Error(`unknown command ${command}`);
    }
  } finally {
    await ctx.close().catch(() => {});
  }
  result.finishedAt = new Date().toISOString();
  fs.mkdirSync(path.dirname(outputPath), { recursive: true });
  fs.writeFileSync(outputPath, JSON.stringify(result, null, 2) + '\n');
  console.log(JSON.stringify({ ok: true, command, output: outputPath, identity: result.identity?.slug || null, results: result.results?.length || 0 }));
}

main().catch((error) => {
  console.error(JSON.stringify({ ok: false, error: error.message }));
  process.exit(1);
});
