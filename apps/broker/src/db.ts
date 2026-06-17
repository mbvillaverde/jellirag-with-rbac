import { D1_MAX_PARAMS } from './limits'

// Split an array into chunks of size n.
export function chunk<T>(arr: T[], n: number): T[][] {
  const out: T[][] = []
  for (let i = 0; i < arr.length; i += n) out.push(arr.slice(i, i + n))
  return out
}

// Maximum number of rows that fit in one D1 statement given paramsPerRow,
// honoring the ≤100 bound-parameter limit. At least 1 row per statement.
export function rowsPerStatement(paramsPerRow: number): number {
  return Math.max(1, Math.floor(D1_MAX_PARAMS / paramsPerRow))
}

// Build placeholders "(?,?,?,...)" for n params.
export function placeholders(n: number): string {
  return '(' + Array.from({ length: n }, () => '?').join(',') + ')'
}
