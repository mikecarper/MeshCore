/*
 * Fixed-loop form of the ref10 field products in fe.c. Only the headless
 * STM32 Companion size recipes opt in; other builds keep the unrolled code.
 * The radix alternates 26 and 25 bits. Odd-by-odd products therefore have
 * weight 2, and terms beyond bit 254 fold back with weight 19, exactly as in
 * ref10. Product accumulation and the rounded carry order are unchanged.
 *
 * All loops, branches and array addresses depend only on public limb indices
 * or the public operation (multiply, square, double-square), not input values.
 * Under ref10's 1.65*2^26 / 1.65*2^25 input bounds, even doubled sums fit in
 * signed 64 bits. Outputs are written only after reading every input, so h
 * may alias f or g. No additional persistent RAM or heap is needed.
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
    for (i = 0; i < (int)sizeof(carry_order); ++i) {
        const int index = carry_order[i];
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

void fe_mul(fe h, const fe f, const fe g) {
    fe_compact_product(h, f, g, 0);
}

void fe_sq(fe h, const fe f) {
    fe_compact_product(h, f, f, 0);
}

void fe_sq2(fe h, const fe f) {
    fe_compact_product(h, f, f, 1);
}
