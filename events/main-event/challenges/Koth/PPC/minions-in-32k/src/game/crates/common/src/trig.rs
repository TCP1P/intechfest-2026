//! Deterministic trigonometry.
//!
//! `f32::sin`/`cos`/`atan2` and friends are NOT correctly-rounded by IEEE-754,
//! so different libm implementations (glibc vs musl vs the wasm/browser math)
//! return different last bits. The sim's only cross-target non-determinism came
//! from these calls — every other op it does (`+ - * /`, `sqrt`, scalar
//! `glam::Vec3`) is correctly-rounded and identical on all targets.
//!
//! These replacements use ONLY correctly-rounded f32 operations (`+ - * /`,
//! `.round()`), so they produce bit-identical results on native and wasm. The
//! game angle math goes through here instead of the std methods so replays
//! reproduce bit-exactly across builds (see the determinism mandate in
//! CLAUDE.md). Accuracy is ~1e-7 over the reduced range — imperceptible for
//! aim/movement, and the values become the single canonical deterministic ones.

use core::f32::consts::{FRAC_PI_2, PI};

const INV_HALF_PI: f32 = 2.0 / PI;

/// Taylor sin on the reduced range [-PI/4, PI/4] (through x^9 ≈ 1e-7 error).
#[inline]
fn sin_unit(r: f32) -> f32 {
    let r2 = r * r;
    r * (1.0
        + r2 * (-0.166_666_67
            + r2 * (0.008_333_333_3 + r2 * (-0.000_198_412_7 + r2 * 2.755_731_9e-6))))
}

/// Taylor cos on the reduced range [-PI/4, PI/4] (through x^8 ≈ 1e-7 error).
#[inline]
fn cos_unit(r: f32) -> f32 {
    let r2 = r * r;
    1.0 + r2 * (-0.5 + r2 * (0.041_666_667 + r2 * (-0.001_388_888_9 + r2 * 2.480_158_7e-5)))
}

/// Deterministic `(sin x, cos x)`. Bit-identical on every target.
#[inline]
pub fn sin_cos(x: f32) -> (f32, f32) {
    // Range-reduce to a quadrant: k = round(x / (PI/2)); r = x - k*(PI/2) lands
    // in [-PI/4, PI/4]. `round` and `+ - *` are all correctly-rounded.
    let k = (x * INV_HALF_PI).round();
    let r = x - k * FRAC_PI_2;
    let (s, c) = (sin_unit(r), cos_unit(r));
    // Select by quadrant (k mod 4). `as i64` of a rounded value is exact.
    match (k as i64).rem_euclid(4) {
        0 => (s, c),
        1 => (c, -s),
        2 => (-s, -c),
        _ => (-c, s),
    }
}

/// Deterministic sine.
#[inline]
pub fn sin(x: f32) -> f32 {
    sin_cos(x).0
}

/// Deterministic cosine.
#[inline]
pub fn cos(x: f32) -> f32 {
    sin_cos(x).1
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matches_std_within_tolerance() {
        // sweep the whole game angle range and a bit beyond
        let mut x = -7.0f32;
        while x <= 7.0 {
            let (s, c) = sin_cos(x);
            assert!(
                (s - x.sin()).abs() < 1e-5,
                "sin({x}) off: {s} vs {}",
                x.sin()
            );
            assert!(
                (c - x.cos()).abs() < 1e-5,
                "cos({x}) off: {c} vs {}",
                x.cos()
            );
            // Pythagorean identity stays tight.
            assert!((s * s + c * c - 1.0).abs() < 1e-4, "sin^2+cos^2 off at {x}");
            x += 0.013;
        }
    }

    #[test]
    fn exact_at_cardinals() {
        for (x, es, ec) in [
            (0.0f32, 0.0f32, 1.0f32),
            (FRAC_PI_2, 1.0, 0.0),
            (PI, 0.0, -1.0),
            (-FRAC_PI_2, -1.0, 0.0),
        ] {
            let (s, c) = sin_cos(x);
            assert!(
                (s - es).abs() < 1e-6 && (c - ec).abs() < 1e-6,
                "cardinal {x}"
            );
        }
    }

    // Determinism guard: these EXACT bit patterns must hold on every target
    // (native + wasm). If a build computes them differently, replays diverge —
    // that is exactly the bug this module exists to prevent. Values are locked
    // from the deterministic polynomial; a legitimate accuracy change updates
    // them deliberately. CI should run this test on wasm32 too.
    #[test]
    fn bit_pinned() {
        // (x, sin.to_bits(), cos.to_bits()) — locked from the deterministic
        // polynomial. A target/build that computes different bits diverges on
        // replay; that is the bug this module prevents. Run on wasm32 in CI too.
        let cases: [(f32, u32, u32); 5] = [
            (0.5, 0x3ef5_7744, 0x3f60_a940),
            (1.234_5, 0x3f71_a8e4, 0x3ea8_f4dd),
            (-2.5, 0xbf19_3579, 0xbf4d_17bf),
            (3.0, 0x3e10_81c9, 0xbf7d_7026),
            (0.123_456, 0x3dfc_3234, 0x3f7e_0d34),
        ];
        for (x, es, ec) in cases {
            let (s, c) = sin_cos(x);
            assert_eq!(s.to_bits(), es, "sin({x}) bits drifted");
            assert_eq!(c.to_bits(), ec, "cos({x}) bits drifted");
        }
    }
}
