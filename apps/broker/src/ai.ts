import { assertEmbedText } from './limits'

export const EMBED_MODEL = '@cf/baai/bge-small-en-v1.5'
export const LLM_MODEL = '@cf/meta/llama-3.1-8b-instruct-fast'

// Embed a single text via the AI binding. Returns the 384-dim vector.
export async function embedOne(ai: Ai, text: string): Promise<number[]> {
  assertEmbedText(text)
  const out = (await ai.run(EMBED_MODEL, { text: [text] })) as {
    data: number[][]
    shape: number[]
  }
  if (!out?.data?.length) throw new Error('embedding produced no vector')
  return out.data[0]
}

// Embed a batch of texts. Each text is validated against the ~512-token limit.
export async function embedBatch(ai: Ai, texts: string[]): Promise<number[][]> {
  const clean = texts.map((t) => assertEmbedText(t))
  const out = (await ai.run(EMBED_MODEL, { text: clean })) as {
    data: number[][]
    shape: number[]
  }
  if (!out?.data?.length) throw new Error('embedding produced no vectors')
  return out.data
}
