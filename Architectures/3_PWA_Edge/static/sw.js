const CACHE_NAME = 'docextract-pwa-v1';
const ASSETS_TO_CACHE = [
  '/',
  '/static/manifest.json',
  '/static/sw.js'
];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => {
      return cache.addAll(ASSETS_TO_CACHE);
    })
  );
});

self.addEventListener('fetch', event => {
  // Pass through API requests, only cache static/HTML assets
  if (event.request.url.includes('/api/')) {
    event.respondWith(fetch(event.request).catch(() => {
        return new Response(JSON.stringify({error: "Offline mode active, request saved for sync"}), {
            headers: {'Content-Type': 'application/json'}
        });
    }));
    return;
  }
  
  event.respondWith(
    caches.match(event.request).then(response => {
      return response || fetch(event.request);
    })
  );
});
