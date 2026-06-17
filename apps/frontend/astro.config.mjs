// @ts-check
import { defineConfig } from 'astro/config';
import node from '@astrojs/node';
import vue from '@astrojs/vue';

// https://astro.build/config
// SSR shell: Astro renders the page server-side, the Vue 3 chat island hydrates
// with `client:load`. Output is served by the @astrojs/node adapter (standalone)
// behind Traefik on :3000.
export default defineConfig({
  output: 'server',
  adapter: node({
    mode: 'standalone',
  }),
  integrations: [vue()],
  server: {
    host: true,
    port: 3000,
  },
});
