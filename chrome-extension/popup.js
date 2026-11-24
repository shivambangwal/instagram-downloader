const LOCAL_IMPORT_URL = 'http://127.0.0.1:8766/api/extension/import';
const LOCAL_APP_URL = 'http://127.0.0.1:8766/';
const scanButton = document.querySelector('#scan');
const statusNode = document.querySelector('#status');

function scanVisibleProfile() {
  return (async () => {
    const pageUrl = new URL(location.href);
    const host = pageUrl.hostname.toLowerCase().replace(/^www\./, '');
    if (host !== 'instagram.com') throw new Error('Open instagram.com first.');
    const parts = pageUrl.pathname.split('/').filter(Boolean);
    const reserved = new Set(['p','reel','reels','tv','stories','share','accounts','explore','direct','about','developer','legal','web']);
    if (!parts.length || reserved.has(parts[0].toLowerCase())) {
      throw new Error('Open a profile page, not a Reel, post, or Stories page.');
    }

    const originalScroll = window.scrollY;
    const reels = new Map();
    let unchanged = 0;
    try {
      for (let round = 0; round < 80 && reels.size < 300 && unchanged < 5; round++) {
        const beforeCount = reels.size;
        for (const anchor of document.querySelectorAll('a[href]')) {
          try {
            const link = new URL(anchor.href, location.origin);
            const match = link.pathname.match(/^\/reel\/([^/]+)\/?$/i);
            if (link.hostname.replace(/^www\./i, '').toLowerCase() !== 'instagram.com' || !match) continue;
            const clean = `https://www.instagram.com/reel/${match[1]}/`;
            if (!reels.has(clean)) reels.set(clean, true);
            if (reels.size >= 300) break;
          } catch (_) { /* Ignore malformed or non-Instagram links. */ }
        }
        const beforeHeight = document.documentElement.scrollHeight;
        window.scrollTo(0, beforeHeight);
        await new Promise(resolve => setTimeout(resolve, 450));
        const noNewLinks = reels.size === beforeCount;
        const noPageGrowth = document.documentElement.scrollHeight <= beforeHeight;
        unchanged = noNewLinks && noPageGrowth ? unchanged + 1 : 0;
      }
    } finally {
      window.scrollTo(0, originalScroll);
    }
    if (!reels.size) throw new Error('No Reel links appeared on this profile. Make sure its Reels grid is visible and try again.');
    return {
      profile_url: `https://www.instagram.com/${parts[0]}/`,
      username: parts[0],
      urls: [...reels.keys()],
    };
  })();
}

scanButton.addEventListener('click', async () => {
  scanButton.disabled = true;
  statusNode.textContent = 'Scanning the profile’s Reel grid… Instagram may take a few seconds to load more.';
  try {
    const [tab] = await chrome.tabs.query({active:true, currentWindow:true});
    const activeUrl = tab?.url ? new URL(tab.url) : null;
    if (!tab?.id || activeUrl?.protocol !== 'https:' || !['instagram.com','www.instagram.com'].includes(activeUrl.hostname.toLowerCase())) {
      throw new Error('Open an Instagram profile in the active tab first.');
    }
    const [result] = await chrome.scripting.executeScript({target:{tabId:tab.id}, func:scanVisibleProfile});
    if (result?.error) throw new Error(result.error.message || 'Could not scan this profile.');
    const profile = result?.result;
    if (!profile?.urls?.length) throw new Error('No Reel links were found.');
    statusNode.textContent = `Found ${profile.urls.length} Reels. Sending them to the local app…`;
    const response = await fetch(LOCAL_IMPORT_URL, {
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(profile),
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || 'The local app rejected the Reel list.');
    statusNode.textContent = `Sent ${payload.count} Reels. Opening the local app…`;
    await chrome.tabs.create({url:LOCAL_APP_URL});
  } catch (error) {
    const message = error.message || String(error);
    statusNode.textContent = message.includes('Failed to fetch')
      ? 'Could not reach the local app. Start Instagram Downloader, then scan again.'
      : message;
  } finally {
    scanButton.disabled = false;
  }
});
