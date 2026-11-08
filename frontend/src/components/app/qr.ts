// A minimal QR Code encoder: byte mode (UTF-8), versions 1 to 6, error correction M (falling back to L
// when the text is too long for M). Enough for a short public link; returns null beyond version 6 so
// the caller shows the link alone. Follows ISO/IEC 18004 (the structure of Nayuki's reference encoder).

type Ecl = "M" | "L";

// Index 0 is version 1.
const TOTAL_CODEWORDS = [26, 44, 70, 100, 134, 172];
const ECC_PER_BLOCK: Record<Ecl, number[]> = { L: [7, 10, 15, 20, 26, 18], M: [10, 16, 26, 18, 24, 16] };
const NUM_BLOCKS: Record<Ecl, number[]> = { L: [1, 1, 1, 1, 1, 2], M: [1, 1, 1, 2, 2, 4] };
const ECL_BITS: Record<Ecl, number> = { L: 1, M: 0 };
const MAX_VERSION = 6;

const dataCapacity = (v: number, ecl: Ecl) => TOTAL_CODEWORDS[v - 1]! - ECC_PER_BLOCK[ecl][v - 1]! * NUM_BLOCKS[ecl][v - 1]!;

// --- GF(256) Reed-Solomon, primitive polynomial 0x11D ---

function gfMul(x: number, y: number): number {
  let z = 0;
  for (let i = 7; i >= 0; i--) {
    z = (z << 1) ^ ((z >>> 7) * 0x11d);
    z ^= ((y >>> i) & 1) * x;
  }
  return z & 0xff;
}

function rsDivisor(degree: number): number[] {
  const result = new Array<number>(degree).fill(0);
  result[degree - 1] = 1;
  let root = 1;
  for (let i = 0; i < degree; i++) {
    for (let j = 0; j < degree; j++) {
      result[j] = gfMul(result[j]!, root);
      if (j + 1 < degree) result[j] = result[j]! ^ result[j + 1]!;
    }
    root = gfMul(root, 0x02);
  }
  return result;
}

export function rsRemainder(data: readonly number[], divisor: readonly number[]): number[] {
  const result = new Array<number>(divisor.length).fill(0);
  for (const b of data) {
    const factor = b ^ result.shift()!;
    result.push(0);
    divisor.forEach((coef, i) => (result[i] = result[i]! ^ gfMul(coef, factor)));
  }
  return result;
}

// --- Data codewords ---

function dataCodewords(bytes: Uint8Array, v: number, ecl: Ecl): number[] {
  const cap = dataCapacity(v, ecl) * 8;
  const bits: number[] = [];
  const put = (val: number, len: number) => {
    for (let i = len - 1; i >= 0; i--) bits.push((val >>> i) & 1);
  };
  put(0b0100, 4); // byte mode
  put(bytes.length, 8); // character count, 8 bits for versions 1..9
  bytes.forEach((b) => put(b, 8));
  put(0, Math.min(4, cap - bits.length)); // terminator
  put(0, (8 - (bits.length % 8)) % 8);
  const out: number[] = [];
  for (let i = 0; i < bits.length; i += 8) out.push(bits.slice(i, i + 8).reduce((a, b) => (a << 1) | b, 0));
  for (let pad = 0xec; out.length < cap / 8; pad ^= 0xec ^ 0x11) out.push(pad);
  return out;
}

function withEcc(data: number[], v: number, ecl: Ecl): number[] {
  const numBlocks = NUM_BLOCKS[ecl][v - 1]!;
  const eccLen = ECC_PER_BLOCK[ecl][v - 1]!;
  const raw = TOTAL_CODEWORDS[v - 1]!;
  const numShort = numBlocks - (raw % numBlocks);
  const shortLen = Math.floor(raw / numBlocks);
  const div = rsDivisor(eccLen);
  const blocks: number[][] = [];
  for (let i = 0, k = 0; i < numBlocks; i++) {
    const dat = data.slice(k, k + shortLen - eccLen + (i < numShort ? 0 : 1));
    k += dat.length;
    const ecc = rsRemainder(dat, div);
    if (i < numShort) dat.push(0);
    blocks.push(dat.concat(ecc));
  }
  const out: number[] = [];
  for (let i = 0; i < blocks[0]!.length; i++)
    blocks.forEach((b, j) => {
      if (i !== shortLen - eccLen || j >= numShort) out.push(b[i]!);
    });
  return out;
}

// --- Matrix ---

class Matrix {
  readonly size: number;
  readonly dark: boolean[][];
  readonly fn: boolean[][];
  constructor(readonly version: number) {
    this.size = version * 4 + 17;
    this.dark = Array.from({ length: this.size }, () => new Array<boolean>(this.size).fill(false));
    this.fn = Array.from({ length: this.size }, () => new Array<boolean>(this.size).fill(false));
  }
  setFn(x: number, y: number, dark: boolean) {
    this.dark[y]![x] = dark;
    this.fn[y]![x] = true;
  }
}

function drawFunctionPatterns(m: Matrix) {
  const n = m.size;
  for (let i = 0; i < n; i++) {
    m.setFn(6, i, i % 2 === 0);
    m.setFn(i, 6, i % 2 === 0);
  }
  for (const [cx, cy] of [
    [3, 3],
    [n - 4, 3],
    [3, n - 4],
  ] as const) {
    for (let dy = -4; dy <= 4; dy++)
      for (let dx = -4; dx <= 4; dx++) {
        const x = cx + dx;
        const y = cy + dy;
        if (x < 0 || y < 0 || x >= n || y >= n) continue;
        const d = Math.max(Math.abs(dx), Math.abs(dy));
        m.setFn(x, y, d !== 2 && d !== 4);
      }
  }
  // versions 2..6 have one alignment pattern, at (4v + 10, 4v + 10)
  if (m.version >= 2) {
    const c = m.version * 4 + 10;
    for (let dy = -2; dy <= 2; dy++)
      for (let dx = -2; dx <= 2; dx++) m.setFn(c + dx, c + dy, Math.max(Math.abs(dx), Math.abs(dy)) !== 1);
  }
  drawFormatBits(m, "M", 0); // reserve; redrawn with the chosen mask
}

export function formatBits(ecl: Ecl, mask: number): number {
  const data = (ECL_BITS[ecl] << 3) | mask;
  let rem = data;
  for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537);
  return ((data << 10) | rem) ^ 0x5412;
}

function drawFormatBits(m: Matrix, ecl: Ecl, mask: number) {
  const bits = formatBits(ecl, mask);
  const bit = (i: number) => ((bits >>> i) & 1) !== 0;
  const n = m.size;
  for (let i = 0; i <= 5; i++) m.setFn(8, i, bit(i));
  m.setFn(8, 7, bit(6));
  m.setFn(8, 8, bit(7));
  m.setFn(7, 8, bit(8));
  for (let i = 9; i < 15; i++) m.setFn(14 - i, 8, bit(i));
  for (let i = 0; i < 8; i++) m.setFn(n - 1 - i, 8, bit(i));
  for (let i = 8; i < 15; i++) m.setFn(8, n - 15 + i, bit(i));
  m.setFn(8, n - 8, true); // the dark module
}

function drawCodewords(m: Matrix, codewords: number[]) {
  const n = m.size;
  let i = 0;
  for (let right = n - 1; right >= 1; right -= 2) {
    if (right === 6) right = 5;
    for (let vert = 0; vert < n; vert++)
      for (let j = 0; j < 2; j++) {
        const x = right - j;
        const upward = ((right + 1) & 2) === 0;
        const y = upward ? n - 1 - vert : vert;
        if (!m.fn[y]![x] && i < codewords.length * 8) {
          m.dark[y]![x] = ((codewords[i >>> 3]! >>> (7 - (i & 7))) & 1) !== 0;
          i++;
        }
      }
  }
}

const MASKS: Array<(x: number, y: number) => boolean> = [
  (x, y) => (x + y) % 2 === 0,
  (_x, y) => y % 2 === 0,
  (x) => x % 3 === 0,
  (x, y) => (x + y) % 3 === 0,
  (x, y) => (Math.floor(x / 3) + Math.floor(y / 2)) % 2 === 0,
  (x, y) => ((x * y) % 2) + ((x * y) % 3) === 0,
  (x, y) => (((x * y) % 2) + ((x * y) % 3)) % 2 === 0,
  (x, y) => (((x + y) % 2) + ((x * y) % 3)) % 2 === 0,
];

function applyMask(m: Matrix, mask: number) {
  const f = MASKS[mask]!;
  for (let y = 0; y < m.size; y++) for (let x = 0; x < m.size; x++) if (!m.fn[y]![x] && f(x, y)) m.dark[y]![x] = !m.dark[y]![x];
}

/** The spec's four penalty rules, so the chosen mask is the easiest to scan. */
function penalty(m: Matrix): number {
  const n = m.size;
  const at = (x: number, y: number) => m.dark[y]![x]!;
  let score = 0;
  const finderA = [true, false, true, true, true, false, true, false, false, false, false];
  const finderB = [false, false, false, false, true, false, true, true, true, false, true];
  for (let pass = 0; pass < 2; pass++)
    for (let a = 0; a < n; a++) {
      const cell = (b: number) => (pass === 0 ? at(b, a) : at(a, b));
      let run = 1;
      for (let b = 1; b <= n; b++) {
        if (b < n && cell(b) === cell(b - 1)) run++;
        else {
          if (run >= 5) score += 3 + (run - 5);
          run = 1;
        }
      }
      for (let b = 0; b + 11 <= n; b++) {
        const hit = (p: boolean[]) => p.every((v, k) => cell(b + k) === v);
        if (hit(finderA) || hit(finderB)) score += 40;
      }
    }
  for (let y = 0; y < n - 1; y++)
    for (let x = 0; x < n - 1; x++) {
      const c = at(x, y);
      if (c === at(x + 1, y) && c === at(x, y + 1) && c === at(x + 1, y + 1)) score += 3;
    }
  let dark = 0;
  for (let y = 0; y < n; y++) for (let x = 0; x < n; x++) if (at(x, y)) dark++;
  const total = n * n;
  score += (Math.ceil(Math.abs(dark * 20 - total * 10) / total) - 1) * 10;
  return score;
}

export interface QrCode {
  version: number;
  ecl: Ecl;
  mask: number;
  size: number;
  /** modules[y][x], true = dark */
  modules: boolean[][];
}

/** Encode `text` as UTF-8 bytes; null when it doesn't fit version 6 at level L. */
export function encodeQr(text: string, forceMask?: number): QrCode | null {
  const bytes = new TextEncoder().encode(text);
  for (const ecl of ["M", "L"] as const)
    for (let v = 1; v <= MAX_VERSION; v++) {
      if (12 + bytes.length * 8 > dataCapacity(v, ecl) * 8) continue;
      const codewords = withEcc(dataCodewords(bytes, v, ecl), v, ecl);
      let best: QrCode | null = null;
      let bestScore = Infinity;
      for (let mask = 0; mask < 8; mask++) {
        if (forceMask !== undefined && mask !== forceMask) continue;
        const m = new Matrix(v);
        drawFunctionPatterns(m);
        drawCodewords(m, codewords);
        applyMask(m, mask);
        drawFormatBits(m, ecl, mask);
        const s = penalty(m);
        if (s < bestScore) {
          bestScore = s;
          best = { version: v, ecl, mask, size: m.size, modules: m.dark };
        }
      }
      return best;
    }
  return null;
}

/** One SVG path ("M x y h1 v1 h-1 z" per dark module, merged per row run) with a 4-module quiet zone. */
export function qrPath(qr: QrCode, quiet = 4): string {
  let d = "";
  qr.modules.forEach((row, y) => {
    for (let x = 0; x < qr.size; x++) {
      if (!row[x]) continue;
      let w = 1;
      while (x + w < qr.size && row[x + w]) w++;
      d += `M${x + quiet} ${y + quiet}h${w}v1h-${w}z`;
      x += w - 1;
    }
  });
  return d;
}
