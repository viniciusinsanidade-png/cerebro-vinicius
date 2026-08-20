// Cérebro do Vinicius — service worker
// Estratégia: network-first (sempre tenta buscar a versão mais nova online;
// só usa o cache quando estiver offline). Isso prioriza dado fresco sobre
// velocidade, o que faz sentido pra um painel que muda com frequência.
const CACHE_NAME = 'cerebro-v1';
const SHELL = ['./', './index.html', './manifest.json', './icon-192.png', './icon-512.png', './data.json'];

self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME).then(cache => cache.addAll(SHELL)).catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    caches.keys().then(names =>
      Promise.all(names.filter(n => n !== CACHE_NAME).map(n => caches.delete(n)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', event => {
  if (event.request.method !== 'GET') return;
  event.respondWith(
    fetch(event.request)
      .then(resp => {
        const copy = resp.clone();
        caches.open(CACHE_NAME).then(cache => cache.put(event.request, copy)).catch(() => {});
        return resp;
      })
      .catch(() => caches.match(event.request).then(cached => cached || caches.match('./index.html')))
  );
});
