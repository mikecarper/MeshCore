/*
 * Fixed-loop form of the ref10 field arithmetic in fe.c. The existing STM32
 * size recipes opt in; other builds keep the unrolled code.
 * The radix alternates 26 and 25 bits. Odd-by-odd products therefore have
 * weight 2, and terms beyond bit 254 fold back with weight 19, exactly as in
 * ref10. Product accumulation, decoding, encoding and carry orders are unchanged.
 *
 * All loops, branches and array addresses depend only on public limb indices
 * or the public operation (multiply, square, double-square), not input values.
 * Under ref10's 1.65*2^26 / 1.65*2^25 input bounds, even doubled sums fit in
 * signed 64 bits. Outputs are written only after reading every input, so h
 * may alias f or g. No additional persistent RAM or heap is needed. Compared
 * with the unrolled forms, public-index loop overhead trades CPU time for
 * flash; the fixed number of field operations and secret-independent control
 * flow are retained for every opted-in Companion and Mini Repeater profile.
 */

void fe_add(fe h, const fe f, const fe g) {
    int i;
    for (i = 0; i < 10; ++i) h[i] = f[i] + g[i];
}

void fe_sub(fe h, const fe f, const fe g) {
    int i;
    for (i = 0; i < 10; ++i) h[i] = f[i] - g[i];
}

void fe_neg(fe h, const fe f) {
    int i;
    for (i = 0; i < 10; ++i) h[i] = -f[i];
}

void fe_copy(fe h, const fe f) {
    int i;
    for (i = 0; i < 10; ++i) h[i] = f[i];
}

void fe_cmov(fe f, const fe g, unsigned int b) {
    const int32_t mask = -(int32_t)b;
    int i;
    for (i = 0; i < 10; ++i) f[i] ^= mask & (f[i] ^ g[i]);
}

void fe_cswap(fe f, fe g, unsigned int b) {
    const int32_t mask = -(int32_t)b;
    int i;
    for (i = 0; i < 10; ++i) {
        const int32_t swap = mask & (f[i] ^ g[i]);
        f[i] ^= swap;
        g[i] ^= swap;
    }
}

static void fe_compact_carry(fe h, int64_t limbs[10],
                             const unsigned char *order, int count) {
    int i;
    for (i = 0; i < count; ++i) {
        const int index = order[i];
        const int bits = 26 - (index & 1);
        const int64_t radix = (int64_t)1 << bits;
        const int64_t carry = (limbs[index] + radix / 2) >> bits;
        if (index == 9) limbs[0] += carry * 19;
        else limbs[index + 1] += carry;
        /* A carry may be negative; do not left-shift that signed value. */
        limbs[index] -= carry * radix;
    }
    for (i = 0; i < 10; ++i) h[i] = (int32_t)limbs[i];
}

static const unsigned char fe_decode_carries[] = {9, 1, 3, 5, 7, 0, 2, 4, 6, 8};

void fe_frombytes(fe h, const unsigned char *s) {
    static const unsigned char offsets[] = {0, 4, 7, 10, 13, 16, 20, 23, 26, 29};
    static const unsigned char shifts[] = {0, 6, 5, 3, 2, 0, 7, 5, 4, 2};
    int64_t limbs[10];
    int i, j;
    for (i = 0; i < 10; ++i) {
        uint32_t word = 0;
        const int count = i == 0 || i == 5 ? 4 : 3;
        for (j = 0; j < count; ++j)
            word |= (uint32_t)s[offsets[i] + j] << (j * 8);
        if (i == 9) word &= 8388607; /* Ignore the encoded sign bit. */
        limbs[i] = (int64_t)word << shifts[i];
    }
    fe_compact_carry(h, limbs, fe_decode_carries, sizeof(fe_decode_carries));
}

void fe_mul121666(fe h, fe f) {
    int64_t limbs[10];
    int i;
    for (i = 0; i < 10; ++i) limbs[i] = (int64_t)f[i] * 121666;
    fe_compact_carry(h, limbs, fe_decode_carries, sizeof(fe_decode_carries));
}

void fe_tobytes(unsigned char *s, const fe h) {
    int32_t limbs[10], q = (19 * h[9] + (1 << 24)) >> 25;
    int i, index = 0, offset = 0;
    for (i = 0; i < 10; ++i) {
        limbs[i] = h[i];
        q = (limbs[i] + q) >> (26 - (i & 1));
    }
    limbs[0] += 19 * q;
    for (i = 0; i < 10; ++i) {
        const int bits = 26 - (i & 1);
        const int32_t carry = limbs[i] >> bits;
        if (i < 9) limbs[i + 1] += carry;
        limbs[i] -= carry * ((int32_t)1 << bits);
    }
    /* Each byte crosses at most one public radix boundary. Normalized limbs
     * are nonnegative, and unsigned shifts avoid overflowing signed int32. */
    for (i = 0; i < 32; ++i) {
        uint32_t byte = (uint32_t)limbs[index] >> offset;
        offset += 8;
        if (offset >= 26 - (index & 1)) {
            offset -= 26 - (index & 1);
            ++index;
            if (offset && index < 10)
                byte |= (uint32_t)limbs[index] << (8 - offset);
        }
        s[i] = (unsigned char)byte;
    }
}

int fe_isnonzero(const fe f) {
    unsigned char bytes[32], value = 0;
    int i;
    fe_tobytes(bytes, f);
    for (i = 0; i < 32; ++i) value |= bytes[i];
    return value != 0;
}

static void fe_compact_product(fe h, const fe f, const fe g, int doubled) {
    static const unsigned char carry_order[] = {0, 4, 1, 5, 2, 6, 3, 7, 4, 8, 9, 0};
    int64_t limbs[10] = {0};
    int i, j;
    for (i = 0; i < 10; ++i) {
        for (j = 0; j < 10; ++j) {
            const int sum = i + j;
            const int index = sum < 10 ? sum : sum - 10;
            const int weight = (sum < 10 ? 1 : 19) * (1 + (i & j & 1));
            limbs[index] += (int64_t)f[i] * g[j] * weight;
        }
    }
    if (doubled) {
        for (i = 0; i < 10; ++i) limbs[i] += limbs[i];
    }
    fe_compact_carry(h, limbs, carry_order, sizeof(carry_order));
}

void fe_mul(fe h, const fe f, const fe g) {
    fe_compact_product(h, f, g, 0);
}

void fe_sq(fe h, const fe f) {
    fe_compact_product(h, f, f, 0);
}

void fe_sq2(fe h, const fe f) {
    fe_compact_product(h, f, f, 1);
}

static void fe_compact_square_n(fe out, const fe in, int count) {
    int i;
    fe_sq(out, in);
    for (i = 1; i < count; ++i) fe_sq(out, out);
}

/* Both ref10 exponentiation chains share z^(2^250-1). Keep the same field
 * operations and carry order, while storing their common code only once.
 * Every squaring count is public and fixed. z11 is retained for inversion.
 */
static void fe_compact_pow250(fe out, fe z11, const fe z) {
    fe t0, t1, t2;
    fe_sq(t0, z);
    fe_compact_square_n(t1, t0, 2);
    fe_mul(t1, z, t1);
    fe_mul(z11, t0, t1);
    fe_sq(t0, z11);
    fe_mul(t0, t1, t0);                  /* 2^5 - 1 */
    fe_compact_square_n(t1, t0, 5);
    fe_mul(t0, t1, t0);                  /* 2^10 - 1 */
    fe_compact_square_n(t1, t0, 10);
    fe_mul(t1, t1, t0);                  /* 2^20 - 1 */
    fe_compact_square_n(t2, t1, 20);
    fe_mul(t1, t2, t1);                  /* 2^40 - 1 */
    fe_compact_square_n(t1, t1, 10);
    fe_mul(t0, t1, t0);                  /* 2^50 - 1 */
    fe_compact_square_n(t1, t0, 50);
    fe_mul(t1, t1, t0);                  /* 2^100 - 1 */
    fe_compact_square_n(t2, t1, 100);
    fe_mul(t1, t2, t1);                  /* 2^200 - 1 */
    fe_compact_square_n(t1, t1, 50);
    fe_mul(out, t1, t0);                 /* 2^250 - 1 */
}

void fe_invert(fe out, const fe z) {
    fe power, z11;
    fe_compact_pow250(power, z11, z);
    fe_compact_square_n(power, power, 5);
    fe_mul(out, power, z11);             /* 2^255 - 21 */
}

void fe_pow22523(fe out, const fe z) {
    fe power, z11;
    fe_compact_pow250(power, z11, z);
    fe_compact_square_n(power, power, 2);
    fe_mul(out, power, z);               /* 2^252 - 3 */
}
