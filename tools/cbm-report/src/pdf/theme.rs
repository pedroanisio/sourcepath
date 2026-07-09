//! Visual constants — the validated reference palette from the dataviz design
//! method (light mode; a print PDF is a light-surface artifact). Categorical
//! slot order is the CVD-safety mechanism: assign in order, never cycle.
//! Since printpdf 0.6 has no alpha, every "translucent" tone is pre-blended
//! over the surface color.

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Rgb01 {
    pub r: f64,
    pub g: f64,
    pub b: f64,
}

pub const fn rgb(hex: u32) -> Rgb01 {
    Rgb01 {
        r: ((hex >> 16) & 0xff) as f64 / 255.0,
        g: ((hex >> 8) & 0xff) as f64 / 255.0,
        b: (hex & 0xff) as f64 / 255.0,
    }
}

/// `alpha`·fg over the surface — the no-alpha-channel substitute for opacity.
pub fn blend_over_surface(fg: Rgb01, alpha: f64) -> Rgb01 {
    let s = SURFACE;
    Rgb01 {
        r: fg.r * alpha + s.r * (1.0 - alpha),
        g: fg.g * alpha + s.g * (1.0 - alpha),
        b: fg.b * alpha + s.b * (1.0 - alpha),
    }
}

// Chart chrome & ink (light mode)
pub const SURFACE: Rgb01 = rgb(0xfcfcfb);
pub const INK: Rgb01 = rgb(0x0b0b0b);
pub const INK_SECONDARY: Rgb01 = rgb(0x52514e);
pub const INK_MUTED: Rgb01 = rgb(0x898781);
pub const GRIDLINE: Rgb01 = rgb(0xe1e0d9);
pub const BASELINE: Rgb01 = rgb(0xc3c2b7);
/// rgba(11,11,11,0.10) pre-blended over the surface.
pub const HAIRLINE_BORDER: Rgb01 = rgb(0xe4e4e3);

// Categorical slots, fixed order (worst adjacent CVD dE 24.2 — validated).
pub const SERIES: [Rgb01; 8] = [
    rgb(0x2a78d6), // 1 blue
    rgb(0x1baf7a), // 2 aqua
    rgb(0xeda100), // 3 yellow
    rgb(0x008300), // 4 green
    rgb(0x4a3aa7), // 5 violet
    rgb(0xe34948), // 6 red
    rgb(0xe87ba4), // 7 magenta
    rgb(0xeb6834), // 8 orange
];

// Status palette (reserved for state, never for a series).
pub const STATUS_GOOD: Rgb01 = rgb(0x0ca30c);
pub const STATUS_WARNING: Rgb01 = rgb(0xfab219);
pub const STATUS_SERIOUS: Rgb01 = rgb(0xec835a);
pub const STATUS_CRITICAL: Rgb01 = rgb(0xd03b3b);

// Page geometry (points; A4 portrait)
pub const PAGE_W: f64 = 595.276;
pub const PAGE_H: f64 = 841.89;
pub const MARGIN: f64 = 46.0;
pub const CONTENT_W: f64 = PAGE_W - 2.0 * MARGIN;

// Mark specs (px→pt at 96 dpi: 1 px = 0.75 pt)
pub const BAR_THICKNESS: f64 = 11.0; // ≤ 24 px (18 pt) cap
pub const BAR_END_RADIUS: f64 = 3.0; // 4 px rounded data-end
pub const SURFACE_GAP: f64 = 1.5; // 2 px gap between touching marks
pub const HAIRLINE: f64 = 0.75; // 1 px gridline

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hex_roundtrip() {
        let c = rgb(0x2a78d6);
        assert!((c.r - 42.0 / 255.0).abs() < 1e-9);
        assert!((c.g - 120.0 / 255.0).abs() < 1e-9);
        assert!((c.b - 214.0 / 255.0).abs() < 1e-9);
    }

    #[test]
    fn blend_10pct_black_matches_hairline() {
        let b = blend_over_surface(INK, 0.10);
        // #0b0b0b @10% over #fcfcfb ≈ #e4e4e3
        assert!((b.r - HAIRLINE_BORDER.r).abs() < 0.01);
        assert!((b.g - HAIRLINE_BORDER.g).abs() < 0.01);
    }
}
