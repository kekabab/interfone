const CACHE_NAME = 'interfone-ai-v2';
const ASSETS = [
    '/',
    '/static/manifest.json'
];

self.addEventListener('install', (event) => {
    self.skipWaiting(); // Força a atualização imediata
    event.waitUntil(
        caches.open(CACHE_NAME).then((cache) => {
            return cache.addAll(ASSETS);
        })
    );
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames.map((cacheName) => {
                    if (cacheName !== CACHE_NAME) {
                        return caches.delete(cacheName); // Apaga versão velha
                    }
                })
            );
        }).then(() => self.clients.claim())
    );
});

// Estratégia: Network First (Tenta sempre a internet antes de usar o cache offline)
self.addEventListener('fetch', (event) => {
    event.respondWith(
        fetch(event.request).catch(() => {
            return caches.match(event.request);
        })
    );
});

// Suporte para Web Push se o usuário configurar Firebase posteriormente
self.addEventListener('push', (event) => {
    const data = event.data ? event.data.json() : {};
    const title = data.title || 'Alguém no Interfone!';
    const options = {
        body: data.body || 'Toque para abrir a conversa.',
        icon: 'https://cdn-icons-png.flaticon.com/512/1211/1211477.png',
        badge: 'https://cdn-icons-png.flaticon.com/512/1211/1211477.png',
        vibrate: [500, 110, 500, 110, 450, 110, 200, 110, 170, 40, 450, 110, 200, 110, 170, 40],
        data: { url: '/' }
    };
    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
    event.notification.close();
    event.waitUntil(
        clients.openWindow('/')
    );
});
