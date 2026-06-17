// Cloudflare bindings for the JellieRAG broker Worker.
// Ambient types (Ai, D1Database, VectorizeIndex) come from
// @cloudflare/workers-types (see tsconfig.json). `wrangler types` can also
// generate a CloudflareBindings interface; this is the hand-maintained shape.
export interface Bindings {
  AI: Ai
  INDEX: VectorizeIndex
  DB: D1Database
  // Provisioned via `wrangler secret put` (task 2.4) — never in source.
  BROKER_SECRET: string
}
