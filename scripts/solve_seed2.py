#!/usr/bin/env python3
"""Full seed recovery for the LUAST L3 sample: printable-filter + aa inversion
+ payload scoring, all self-contained."""
import numpy as np

M32 = 0xFFFFFFFF
M31 = 2147483647
C = bytes(b")\x949\x03\x16n\xb2K\x01gnut\r\xa7")
AE = 13593
INV16807 = pow(16807, -1, M31)

c_le1 = int.from_bytes(C[0:4], 'little')
c_le2 = int.from_bytes(C[4:8], 'little')
c_le3 = int.from_bytes(C[8:12], 'little')


def bx_rshift_inv(y, n):
    x = 0
    for i in range(31, -1, -1):
        bit = (y >> i) & 1
        if i + n <= 31:
            bit ^= (x >> (i + n)) & 1
        x |= bit << i
    return x


def bx_lshift_inv(y, n):
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


FS = [
    ('add', 2654435769), ('rs', 16), ('ls', 5), ('rot', 11),
    ('add', 2246822507), ('rs', 13), ('ls', 9), ('rot', 19),
    ('add', 3266489909), ('rs', 15), ('ls', 7), ('rot', 23),
    ('add', 668265263), ('rs', 17), ('ls', 11), ('rot', 13),
    ('add', 374761393), ('rs', 11), ('ls', 13), ('rot', 7),
    ('add', 4283543511), ('rs', 14), ('ls', 6), ('rot', 17),
]


def aa(R):
    R &= M32
    for kind, p in FS:
        if kind == 'add':
            R = (R + p) & M32
        elif kind == 'rs':
            R = (R ^ (R >> p)) & M32
        elif kind == 'ls':
            R = (R ^ ((R << p) & M32)) & M32
        else:
            R = ((R << p) | (R >> (32 - p))) & M32
    return R


def aa_inv(Y):
    Y &= M32
    for kind, p in reversed(FS):
        if kind == 'add':
            Y = (Y - p) & M32
        elif kind == 'rs':
            Y = bx_rshift_inv(Y, p)
        elif kind == 'ls':
            Y = bx_lshift_inv(Y, p)
        else:
            Y = rrot(Y, p)
    return Y


assert aa(aa_inv(123456789)) == 123456789
assert aa_inv(aa(999)) == 999


def decode_with_aq0(aq0):
    aq = aq0
    out = bytearray(C)
    at = 0
    ap = len(C)
    while at <= ap - 4:
        aq = aq * 16807 % M31
        chunk = int.from_bytes(out[at:at + 4], 'little')
        out[at:at + 4] = (chunk ^ (aq & M32)).to_bytes(4, 'little')
        at += 4
    aq = aq * 16807 % M31
    au = 0
    while at < ap:
        out[at] ^= (aq >> (au * 8)) & 255
        at += 1
        au += 1
    return bytes(out)


def main():
    PM = np.zeros(256, dtype=bool)
    for b in range(32, 127):
        PM[b] = True
    for b in (9, 10, 13):
        PM[b] = True

    bases = np.uint64([95 ** i for i in range(4)])
    n = 95 ** 4
    results = []
    CH = 4_000_000
    for start in range(0, n, CH):
        idx = np.arange(start, min(start + CH, n), dtype=np.uint64)
        p = np.zeros(len(idx), dtype=np.uint64)
        for i in range(4):
            p |= ((idx // bases[i]) % np.uint64(95)) << np.uint64(8 * i)
        k1 = np.uint64(c_le1) ^ p
        aq0 = (k1 * np.uint64(INV16807)) % np.uint64(M31)
        k2 = (k1 * np.uint64(16807)) % np.uint64(M31)
        p2 = np.uint64(c_le2) ^ k2
        m = (PM[(p2 & np.uint64(0xFF)).astype(np.int64)]
             & PM[((p2 >> np.uint64(8)) & np.uint64(0xFF)).astype(np.int64)]
             & PM[((p2 >> np.uint64(16)) & np.uint64(0xFF)).astype(np.int64)]
             & PM[((p2 >> np.uint64(24)) & np.uint64(0xFF)).astype(np.int64)])
        if not m.any():
            continue
        k3 = (k2[m] * np.uint64(16807)) % np.uint64(M31)
        p3 = np.uint64(c_le3) ^ k3
        m2 = (PM[(p3 & np.uint64(0xFF)).astype(np.int64)]
              & PM[((p3 >> np.uint64(8)) & np.uint64(0xFF)).astype(np.int64)]
              & PM[((p3 >> np.uint64(16)) & np.uint64(0xFF)).astype(np.int64)]
              & PM[((p3 >> np.uint64(24)) & np.uint64(0xFF)).astype(np.int64)])
        if not m2.any():
            continue
        aq0m = aq0[m][m2]
        k4 = (k3[m2] * np.uint64(16807)) % np.uint64(M31)
        b12 = (np.uint64(C[12]) ^ (k4 & np.uint64(0xFF))).astype(np.int64)
        b13 = (np.uint64(C[13]) ^ ((k4 >> np.uint64(8)) & np.uint64(0xFF))).astype(np.int64)
        b14 = (np.uint64(C[14]) ^ ((k4 >> np.uint64(16)) & np.uint64(0xFF))).astype(np.int64)
        m3 = PM[b12] & PM[b13] & PM[b14]
        for aqv in aq0m[m3]:
            aq0v = int(aqv)
            payload = decode_with_aq0(aq0v)
            for t in range(3):
                v = aq0v - 1 + t * 2147483646
                if v > M32:
                    continue
                ak = aa_inv(v)
                Z = (ak - AE) % M32
                Zs = Z if Z < 2 ** 31 else Z - 2 ** 32
                results.append((Zs, payload))

    print('total (Z, payload) candidates:', len(results))

    def eng_score(p):
        try:
            s = p.decode('ascii')
        except Exception:
            return 0.0
        good = sum(1 for ch in s if ch.isalnum() or ch in " .,!?:;'\"-_()[]+/")
        return good / len(s)

    results.sort(key=lambda r: -eng_score(r[1]))
    seen = set()
    shown = 0
    for Z, payload in results:
        if payload in seen:
            continue
        seen.add(payload)
        e = eng_score(payload)
        if e < 0.7:
            continue
        print('Z=%-13d eng=%.2f payload=%r' % (Z, e, payload))
        shown += 1
        if shown >= 40:
            break


if __name__ == '__main__':
    main()
