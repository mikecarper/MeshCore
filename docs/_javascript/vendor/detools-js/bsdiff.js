/* detools-js 0.1.0; MIT; adapted for Uint8Array and browser. See LICENSE. */
(function(root){
'use strict';

// Pure-JS port of Colin Percival's bsdiff (the algorithm detools' C extension
// is based on): qsufsort suffix array + greedy match, emitting control entries
// applied as `out = (diff[i] + old[i]) & 0xff`, then literal `extra`, then a
// signed `seek`. Output need not match detools byte-for-byte, only decode via
// detools apply_patch.

/**
 * Build the suffix array of `old` into Int32Array I (length oldsize + 1).
 * Direct port of bsdiff's qsufsort.
 */
function qsufsort(old, oldsize) {
  const I = new Int32Array(oldsize + 1);
  const V = new Int32Array(oldsize + 1);
  const buckets = new Int32Array(256);

  for (let i = 0; i < oldsize; i++) buckets[old[i]]++;
  for (let i = 1; i < 256; i++) buckets[i] += buckets[i - 1];
  for (let i = 255; i > 0; i--) buckets[i] = buckets[i - 1];
  buckets[0] = 0;

  for (let i = 0; i < oldsize; i++) I[++buckets[old[i]]] = i;
  I[0] = oldsize;
  for (let i = 0; i < oldsize; i++) V[i] = buckets[old[i]];
  V[oldsize] = 0;
  for (let i = 1; i < 256; i++) {
    if (buckets[i] === buckets[i - 1] + 1) I[buckets[i]] = -1;
  }
  I[0] = -1;

  for (let h = 1; I[0] !== -(oldsize + 1); h += h) {
    let len = 0;
    let i = 0;
    while (i < oldsize + 1) {
      if (I[i] < 0) {
        len -= I[i];
        i -= I[i];
      } else {
        if (len) I[i - len] = -len;
        len = V[I[i]] - i + 1;
        split(I, V, i, len, h);
        i += len;
        len = 0;
      }
    }
    if (len) I[i - len] = -len;
  }

  for (let i = 0; i < oldsize + 1; i++) I[V[i]] = i;

  return I;
}

function split(I, V, start, len, h) {
  let i, j, k, x, tmp;

  if (len < 16) {
    for (k = start; k < start + len; k += j) {
      j = 1;
      x = V[I[k] + h];
      for (i = 1; k + i < start + len; i++) {
        if (V[I[k + i] + h] < x) {
          x = V[I[k + i] + h];
          j = 0;
        }
        if (V[I[k + i] + h] === x) {
          tmp = I[k + j];
          I[k + j] = I[k + i];
          I[k + i] = tmp;
          j++;
        }
      }
      for (i = 0; i < j; i++) V[I[k + i]] = k + j - 1;
      if (j === 1) I[k] = -1;
    }
    return;
  }

  x = V[I[start + (len >> 1)] + h];
  let jj = 0;
  let kk = 0;
  for (i = start; i < start + len; i++) {
    if (V[I[i] + h] < x) jj++;
    if (V[I[i] + h] === x) kk++;
  }
  jj += start;
  kk += jj;

  i = start;
  j = 0;
  k = 0;
  while (i < jj) {
    if (V[I[i] + h] < x) {
      i++;
    } else if (V[I[i] + h] === x) {
      tmp = I[i];
      I[i] = I[jj + j];
      I[jj + j] = tmp;
      j++;
    } else {
      tmp = I[i];
      I[i] = I[kk + k];
      I[kk + k] = tmp;
      k++;
    }
  }

  while (jj + j < kk) {
    if (V[I[jj + j] + h] === x) {
      j++;
    } else {
      tmp = I[jj + j];
      I[jj + j] = I[kk + k];
      I[kk + k] = tmp;
      k++;
    }
  }

  if (jj > start) split(I, V, start, jj - start, h);

  for (i = 0; i < kk - jj; i++) V[I[jj + i]] = kk - 1;
  if (jj === kk - 1) I[jj] = -1;

  if (start + len > kk) split(I, V, kk, start + len - kk, h);
}

function matchlen(old, oldOff, oldsize, neu, newOff, newsize) {
  const max = Math.min(oldsize - oldOff, newsize - newOff);
  let i = 0;
  while (i < max && old[oldOff + i] === neu[newOff + i]) i++;
  return i;
}

// Compare old[I[x]..] with new[newOff..] (lexicographic over min length).
function memcmpOldNew(old, oldOff, oldsize, neu, newOff, newsize) {
  const max = Math.min(oldsize - oldOff, newsize - newOff);
  for (let i = 0; i < max; i++) {
    const a = old[oldOff + i];
    const b = neu[newOff + i];
    if (a < b) return -1;
    if (a > b) return 1;
  }
  return 0;
}

// Iterative binary search over the suffix array, returns { pos, len }.
function search(I, old, oldsize, neu, newOff, newsize) {
  let st = 0;
  let en = oldsize;

  while (en - st >= 2) {
    const x = st + ((en - st) >> 1);
    if (memcmpOldNew(old, I[x], oldsize, neu, newOff, newsize) < 0) {
      st = x;
    } else {
      en = x;
    }
  }

  const xlen = matchlen(old, I[st], oldsize, neu, newOff, newsize);
  const ylen = matchlen(old, I[en], oldsize, neu, newOff, newsize);

  if (xlen > ylen) {
    return { pos: I[st], len: xlen };
  }
  return { pos: I[en], len: ylen };
}

/**
 * Create the bsdiff control entries.
 * @param {Buffer} old
 * @param {Buffer} neu
 * @returns {Array<{diff: Buffer, extra: Buffer, adjustment: number}>}
 */
function createControl(old, neu) {
  const oldsize = old.length;
  const newsize = neu.length;
  const I = qsufsort(old, oldsize);

  const controls = [];

  let scan = 0;
  let len = 0;
  let pos = 0;
  let lastscan = 0;
  let lastpos = 0;
  let lastoffset = 0;

  while (scan < newsize) {
    let oldscore = 0;
    scan += len;

    for (let scsc = scan; scan < newsize; scan++) {
      const r = search(I, old, oldsize, neu, scan, newsize);
      len = r.len;
      pos = r.pos;

      for (; scsc < scan + len; scsc++) {
        if (
          scsc + lastoffset < oldsize &&
          old[scsc + lastoffset] === neu[scsc]
        ) {
          oldscore++;
        }
      }

      if ((len === oldscore && len !== 0) || len > oldscore + 8) {
        break;
      }

      if (
        scan + lastoffset < oldsize &&
        old[scan + lastoffset] === neu[scan]
      ) {
        oldscore--;
      }
    }

    if (len !== oldscore || scan === newsize) {
      // Forward extension.
      let s = 0;
      let Sf = 0;
      let lenf = 0;
      for (let i = 0; lastscan + i < scan && lastpos + i < oldsize; ) {
        if (old[lastpos + i] === neu[lastscan + i]) s++;
        i++;
        if (s * 2 - i > Sf * 2 - lenf) {
          Sf = s;
          lenf = i;
        }
      }

      // Backward extension.
      let lenb = 0;
      if (scan < newsize) {
        s = 0;
        let Sb = 0;
        for (let i = 1; scan >= lastscan + i && pos >= i; i++) {
          if (old[pos - i] === neu[scan - i]) s++;
          if (s * 2 - i > Sb * 2 - lenb) {
            Sb = s;
            lenb = i;
          }
        }
      }

      // Resolve overlap between forward and backward regions.
      if (lastscan + lenf > scan - lenb) {
        const overlap = lastscan + lenf - (scan - lenb);
        s = 0;
        let Ss = 0;
        let lens = 0;
        for (let i = 0; i < overlap; i++) {
          if (neu[lastscan + lenf - overlap + i] === old[lastpos + lenf - overlap + i]) {
            s++;
          }
          if (neu[scan - lenb + i] === old[pos - lenb + i]) {
            s--;
          }
          if (s > Ss) {
            Ss = s;
            lens = i + 1;
          }
        }
        lenf += lens - overlap;
        lenb -= lens;
      }

      // Emit diff bytes: (new[lastscan+i] - old[lastpos+i]) & 0xff
      const diff = new Uint8Array(lenf);
      for (let i = 0; i < lenf; i++) {
        diff[i] = (neu[lastscan + i] - old[lastpos + i]) & 0xff;
      }

      // Emit extra bytes: new[lastscan+lenf .. scan-lenb]
      const extraLen = scan - lenb - (lastscan + lenf);
      const extra = new Uint8Array(extraLen);
      for (let i = 0; i < extraLen; i++) {
        extra[i] = neu[lastscan + lenf + i];
      }

      const adjustment = pos - lenb - (lastpos + lenf);

      controls.push({ diff, extra, adjustment });

      lastscan = scan - lenb;
      lastpos = pos - lenb;
      lastoffset = pos - scan;
    }
  }

  return controls;
}

/**
 * Add two byte buffers element-wise mod 256 (detools bsdiff.add_bytes).
 */
function addBytes(a, b) {
  const out = new Uint8Array(a.length);
  for (let i = 0; i < a.length; i++) {
    out[i] = (a[i] + b[i]) & 0xff;
  }
  return out;
}

const exported = { createControl, addBytes, qsufsort };

if (typeof module === "object" && module.exports) module.exports = exported; else root.MeshCoreDetoolsBsdiff = exported;
})(typeof globalThis !== "undefined" ? globalThis : this);
