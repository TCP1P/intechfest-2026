//! 16.16 fixed-point helpers built on `protocol::Fp`.
//!
//! All gameplay math goes through these so replays reproduce bit-exactly.

use protocol::{Fp, Vec3};

pub const FP_SCALE: i32 = 1 << 16;
pub const FP_ONE: Fp = Fp(FP_SCALE);
pub const FP_ZERO: Fp = Fp(0);

pub trait FpExt: Sized {
    fn from_f32(x: f32) -> Self;
    fn to_f32(self) -> f32;
    fn from_int(x: i32) -> Self;
    fn mul_fp(self, other: Self) -> Self;
    fn div_fp(self, other: Self) -> Self;
    fn add(self, other: Self) -> Self;
    fn sub(self, other: Self) -> Self;
    fn neg(self) -> Self;
}

impl FpExt for Fp {
    #[inline]
    fn from_f32(x: f32) -> Self {
        Fp((x * FP_SCALE as f32) as i32)
    }
    #[inline]
    fn to_f32(self) -> f32 {
        self.0 as f32 / FP_SCALE as f32
    }
    #[inline]
    fn from_int(x: i32) -> Self {
        Fp(x.saturating_mul(FP_SCALE))
    }
    #[inline]
    fn mul_fp(self, other: Self) -> Self {
        Fp(((self.0 as i64 * other.0 as i64) >> 16) as i32)
    }
    #[inline]
    fn div_fp(self, other: Self) -> Self {
        Fp((((self.0 as i64) << 16) / other.0 as i64) as i32)
    }
    #[inline]
    fn add(self, other: Self) -> Self {
        Fp(self.0.wrapping_add(other.0))
    }
    #[inline]
    fn sub(self, other: Self) -> Self {
        Fp(self.0.wrapping_sub(other.0))
    }
    #[inline]
    fn neg(self) -> Self {
        Fp(self.0.wrapping_neg())
    }
}

pub trait Vec3Ext: Sized {
    fn zero() -> Self;
    fn from_f32(x: f32, y: f32, z: f32) -> Self;
    fn add(self, other: Self) -> Self;
    fn sub(self, other: Self) -> Self;
    fn scale(self, s: Fp) -> Self;
}

impl Vec3Ext for Vec3 {
    #[inline]
    fn zero() -> Self {
        Vec3 {
            x: FP_ZERO,
            y: FP_ZERO,
            z: FP_ZERO,
        }
    }
    #[inline]
    fn from_f32(x: f32, y: f32, z: f32) -> Self {
        Vec3 {
            x: Fp::from_f32(x),
            y: Fp::from_f32(y),
            z: Fp::from_f32(z),
        }
    }
    #[inline]
    fn add(self, other: Self) -> Self {
        Vec3 {
            x: FpExt::add(self.x, other.x),
            y: FpExt::add(self.y, other.y),
            z: FpExt::add(self.z, other.z),
        }
    }
    #[inline]
    fn sub(self, other: Self) -> Self {
        Vec3 {
            x: FpExt::sub(self.x, other.x),
            y: FpExt::sub(self.y, other.y),
            z: FpExt::sub(self.z, other.z),
        }
    }
    #[inline]
    fn scale(self, s: Fp) -> Self {
        Vec3 {
            x: self.x.mul_fp(s),
            y: self.y.mul_fp(s),
            z: self.z.mul_fp(s),
        }
    }
}
