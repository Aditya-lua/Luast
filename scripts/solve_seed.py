#!/usr/bin/env python3
"""Solve for the true decode seed Z of the LUAST L3 sample by inverting
the round function aa and the LCG keystream, assuming a printable payload.

  plaintext[0:4] = cipher[0:4] XOR k1          k1 = aq0 * 16807 % (2^31-1)
  aq0 = aa(ak) % 2147483646 + 1
  ak   = (13593 + Z) mod 2^32

aa is a bijection on uint32 -> ak = aa_inv(aq0 - 1 + t*2147483646), t in 0..2.
"""
import numpy as np

M32 = 0xFFFFFFFF
M31 = 2147483647
C = bytes(b")\x949\x03\x16n\xb2K\x01gnut\r\xa7")
AE = 13593

c_le1 = int.from_bytes(C[0:4], "little")
c_le2 = int.from_bytes(C[4:8], "little")
c_le3 = int.from_bytes(C[8:12], "little")

INV16807 = pow(16807, -1, M31)

PRINT_LO, PRINT_HI = 32, 126
ALLOWED = [32, 33]


def make_print_mask_bytes(col):
    v = np.zeros(256, dtype=bool)
    for b in range(PRINT_LO, PRINT_HI + 1):
        v[b] = True
    for b in (9, 10, 13):
        v[b] = True
    return v


PMASK = make_print_mask_bytes(None)

# 95^4 candidate plaintexts for bytes 0..3
import itertools
ALPHA = [b for b in range(32, 127)] + [9, 10, 13]


def printable_ok(arr):
    return arr


def main():
    bases = np.uint64([95 ** i for i in range(4)])
    dig = np.array([b for b in ALPHA if b >= 32], dtype=np.uint64)  # 95 values
    n = len(dig) ** 4
    print("candidates:", n)

    survivors = []
    CH = 4_000_000
    for start in range(0, n, CH):
        idx = np.arange(start, min(start + CH, n), dtype=np.uint64)
        p = np.zeros(len(idx), dtype=np.uint64)
        for i in range(4):
            p |= ((idx // bases[i]) % np.uint64(95)) << np.uint64(8 * i)
        # p bytes are all in 32..126 by construction
        k1 = np.uint64(c_le1) ^ p
        aq0 = (k1 * np.uint64(INV16807)) % np.uint64(M31)
        if np.any(aq0 == 0):
            continue
        k2 = (k1 * np.uint64(16807)) % np.uint64(M31)
        p2 = np.uint64(c_le2) ^ k2
        b4 = (p2 & np.uint64(0xFF)).astype(np.int64)
        b5 = ((p2 >> np.uint64(8)) & np.uint64(0xFF)).astype(np.int64)
        b6 = ((p2 >> np.uint64(16)) & np.uint64(0xFF)).astype(np.int64)
        b7 = ((p2 >> np.uint64(24)) & np.uint64(0xFF)).astype(np.int64)
        m = PMASK[b4] & PMASK[b5] & PMASK[b6] & PMASK[b7]
        if not m.any():
            continue
        k3 = (k2[m] * np.uint64(16807)) % np.uint64(M31)  # k2 already chained from k1
        p3 = np.uint64(c_le3) ^ k3
        b8 = (p3 & np.uint64(0xFF)).astype(np.int64)
        b9 = ((p3 >> np.uint64(8)) & np.uint64(0xFF)).astype(np.int64)
        b10 = ((p3 >> np.uint64(16)) & np.uint64(0xFF)).astype(np.int64)
        b11 = ((p3 >> np.uint64(24)) & np.uint64(0xFF)).astype(np.int64)
        m2 = PMASK[b8] & PMASK[b9] & PMASK[b10] & PMASK[b11]
        if not m2.any():
            continue
        k1m = k1[m][m2]
        aq0m = aq0[m][m2]
        k4 = (k3[m2] * np.uint64(16807)) % np.uint64(M31)
        b12 = (np.uint64(C[12]) ^ (k4 & np.uint64(0xFF))).astype(np.int64)
        b13 = (np.uint64(C[13]) ^ ((k4 >> np.uint64(8)) & np.uint64(0xFF))).astype(np.int64)
        b14 = (np.uint64(C[14]) ^ ((k4 >> np.uint64(16)) & np.uint64(0xFF))).astype(np.int64)
        m3 = PMASK[b12] & PMASK[b13] & PMASK[b14]
        for aq in aq0m[m3]:
            survivors.append(int(aq))
    print("survivors after printable filter:", len(survivors))

    # ---- invert aa ----
    def bx_rshift_inv(y, n):
        # inverse of Y = X ^ (X >> n): X_i = Y_i ^ X_{i+n}, compute descending
        x = 0
        for i in range(31, -1, -1):
            bit = (y >> i) & 1
            if i + n <= 31:
                bit ^= (x >> (i + n)) & 1
            x |= bit << i
        return x

    def bx_lshift_inv(y, n):
        # inverse of Y = X ^ ((X << n) & M): X_i = Y_i ^ X_{i-n}, ascending
        x = 0
        for i in range(32):
            bit = (y >> i) & 1
            if i - n >= 0:
                bit ^= (x >> (i - n)) & 1
            x |= bit << i
        return x

    def rrot(x, n):
        n %= 32
        return ((x >> n) | (x << (32 - n))) & M32 if n else x

    def aa_inv(y):
        y = rrot(y, 17)
        y = bx_lshift_inv(y, 6)
        y = bx_rshift_inv(y, 14)
        y = (y - 4283543511) & M32
        y = rrot(y, 7)
        y = bx_lshift_inv(y, 13)
        y = bx_rshift_inv(y, 11)
        y = (y - 374761393) & M32
        y = rrot(y, 13)
        y = bx_lshift_inv(y, 11)
        y = bx_rshift_inv(y, 17)
        y = (y - 668265263) & M32
        y = rrot(y, 23)
        y = bx_lshift_inv(y, 7)
        y = bx_rshift_inv(y, 15)
        y = (y - 3266489909) & M32
        y = rrot(y, 19)
        y = bx_lshift_inv(y, 9)
        y = bx_rshift_inv(y, 13)
        y = (y - 2246822507) & M32
        y = rrot(y, 11)
        y = bx_lshift_inv(y, 5)
        y = bx_rshift_inv(y, 16)
        y = (y - 2654435769) & M32
        return y

    # verify inverse
    def aa(R):
        R = (R + 2654435769) & M32
        R = bxor(R, rshift(R, 16)); R = bxor(R, lshift(R, 5)); R = lrot(R, 11)
        R = (R + 2246822507) & M32
        R = bxor(R, rshift(R, 13)); R = bxor(R, lshift(R, 9)); R = lrot(R, 19)
        R = (R + 3266489909) & M32
        R = bxor(R, rshift(R, 15)); R = bxor(R, lshift(R, 7)); R = lrot(R, 23)
        R = (R + 668265263) & M32
        R = bxor(R, rshift(R, 17)); R = bxor(R, lshift(R, 11)); R = lrot(R, 13)
        R = (R + 374761393) & M32
        R = bxor(R, rshift(R, 11)); R = bxor(R, lshift(R, 13)); R = lrot(R, 7)
        R = (R + 4283543511) & M32
        R = bxor(R, rshift(R, 14)); R = bxor(R, lshift(R, 6)); R = lrot(R, 17)
        return R

    assert aa(aa_inv(123456789)) == 123456789
    print("aa_inv verified")

    results = []
    for aq0 in survivors:
        base = aq0 - 1
        for t in range(0, 3):
            v = base + t * 2147483646
            if v > M32:
                continue
            ak = aa_inv(v & M32)
            Z = (ak - AE) % M32
            Zs = Z if Z < 2 ** 31 else Z - 2 ** 32
            results.append((Zs, aq0, t))

    results.sort(key=lambda r: abs(r[0]))
    print("\nplausible Z values (sorted by |Z|):")
    for Zs, aq0, t in results[:40]:
        print("  Z=%-14d aq0=%-11d t=%d" % (Zs, aq0, t))


def bxor(*a):
    r = 0
    for x in a:
        r ^= x & M32
    return r


def rshift(a, n):
    return (a & M32) >> n if n < 32 else 0


def lshift(a, n):
    return ((a & M32) << n) & M32 if n < 32 else 0


def lrot(a, n):
    n %= 32
    a &= M32
    return ((a << n) | (a >> (32 - n))) & M32 if n else a


if __name__ == "__main__":
    main()
