//! A small top-left-origin vector drawing layer over printpdf, with real text
//! measurement (glyph advances from the embedded TTFs via ttf-parser).
//! Geometry is computed in f64 pt and cast to printpdf's f32 at the boundary.

use std::path::PathBuf;

use anyhow::{bail, Context, Result};
use printpdf::{
    Color, IndirectFontRef, Line, Mm, PdfDocumentReference, PdfLayerReference, Point, Polygon,
    PolygonMode, Rgb, WindingOrder,
};

use crate::pdf::theme::{Rgb01, PAGE_H};

const PT_TO_MM: f64 = 25.4 / 72.0;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FontKind {
    Regular,
    Bold,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Align {
    Left,
    Right,
    Center,
}

pub struct Fonts {
    pub regular: IndirectFontRef,
    pub bold: IndirectFontRef,
    regular_data: Vec<u8>,
    bold_data: Vec<u8>,
}

/// Candidate locations for the report faces. `CBM_REPORT_FONT_DIR` overrides.
fn font_paths() -> Vec<(PathBuf, PathBuf)> {
    let mut v = Vec::new();
    if let Ok(dir) = std::env::var("CBM_REPORT_FONT_DIR") {
        let d = PathBuf::from(dir);
        v.push((d.join("DejaVuSans.ttf"), d.join("DejaVuSans-Bold.ttf")));
    }
    let sys = PathBuf::from("/usr/share/fonts/truetype/dejavu");
    v.push((sys.join("DejaVuSans.ttf"), sys.join("DejaVuSans-Bold.ttf")));
    v
}

impl Fonts {
    pub fn load(doc: &PdfDocumentReference) -> Result<Self> {
        for (reg, bold) in font_paths() {
            if reg.is_file() && bold.is_file() {
                let regular_data = std::fs::read(&reg)?;
                let bold_data = std::fs::read(&bold)?;
                let regular = doc
                    .add_external_font(regular_data.as_slice())
                    .context("embed regular font")?;
                let bold_ref = doc
                    .add_external_font(bold_data.as_slice())
                    .context("embed bold font")?;
                return Ok(Self {
                    regular,
                    bold: bold_ref,
                    regular_data,
                    bold_data,
                });
            }
        }
        bail!(
            "DejaVu Sans not found; install fonts-dejavu or set CBM_REPORT_FONT_DIR \
             to a directory containing DejaVuSans.ttf and DejaVuSans-Bold.ttf"
        )
    }

    /// Width of `text` at `size` pt, from real glyph advances.
    pub fn width(&self, text: &str, size: f64, kind: FontKind) -> f64 {
        let data = match kind {
            FontKind::Regular => &self.regular_data,
            FontKind::Bold => &self.bold_data,
        };
        let Ok(face) = ttf_parser::Face::parse(data, 0) else {
            return text.chars().count() as f64 * size * 0.55; // metric fallback
        };
        let upem = face.units_per_em() as f64;
        let fallback = face
            .glyph_index(' ')
            .and_then(|g| face.glyph_hor_advance(g))
            .unwrap_or((upem * 0.5) as u16);
        let units: u64 = text
            .chars()
            .map(|c| {
                face.glyph_index(c)
                    .and_then(|g| face.glyph_hor_advance(g))
                    .unwrap_or(fallback) as u64
            })
            .sum();
        units as f64 / upem * size
    }
}

/// One page with a top-left-origin pt coordinate system.
pub struct Canvas<'a> {
    pub layer: PdfLayerReference,
    pub fonts: &'a Fonts,
}

impl<'a> Canvas<'a> {
    pub fn new(layer: PdfLayerReference, fonts: &'a Fonts) -> Self {
        Self { layer, fonts }
    }

    fn pt(x: f64, y_top: f64) -> Point {
        Point::new(
            Mm((x * PT_TO_MM) as f32),
            Mm(((PAGE_H - y_top) * PT_TO_MM) as f32),
        )
    }

    fn set_fill(&self, c: Rgb01) {
        self.layer
            .set_fill_color(Color::Rgb(Rgb::new(c.r as f32, c.g as f32, c.b as f32, None)));
    }

    fn set_stroke(&self, c: Rgb01, width: f64) {
        self.layer
            .set_outline_color(Color::Rgb(Rgb::new(c.r as f32, c.g as f32, c.b as f32, None)));
        self.layer.set_outline_thickness(width as f32);
    }

    fn fill_poly(&self, points: Vec<Point>) {
        self.layer.add_polygon(Polygon {
            rings: vec![points.into_iter().map(|p| (p, false)).collect()],
            mode: PolygonMode::Fill,
            winding_order: WindingOrder::NonZero,
        });
    }

    fn stroke_path(&self, points: Vec<Point>, closed: bool) {
        self.layer.add_line(Line {
            points: points.into_iter().map(|p| (p, false)).collect(),
            is_closed: closed,
        });
    }

    pub fn rect(&self, x: f64, y: f64, w: f64, h: f64, color: Rgb01) {
        self.set_fill(color);
        self.fill_poly(vec![
            Self::pt(x, y),
            Self::pt(x + w, y),
            Self::pt(x + w, y + h),
            Self::pt(x, y + h),
        ]);
    }

    pub fn rect_outline(&self, x: f64, y: f64, w: f64, h: f64, color: Rgb01, width: f64) {
        self.set_stroke(color, width);
        self.stroke_path(
            vec![
                Self::pt(x, y),
                Self::pt(x + w, y),
                Self::pt(x + w, y + h),
                Self::pt(x, y + h),
            ],
            true,
        );
    }

    pub fn hline(&self, x0: f64, x1: f64, y: f64, color: Rgb01, width: f64) {
        self.set_stroke(color, width);
        self.stroke_path(vec![Self::pt(x0, y), Self::pt(x1, y)], false);
    }

    pub fn vline(&self, x: f64, y0: f64, y1: f64, color: Rgb01, width: f64) {
        self.set_stroke(color, width);
        self.stroke_path(vec![Self::pt(x, y0), Self::pt(x, y1)], false);
    }

    /// Quarter-arc polyline (8 segments — visually a circle at chart radii).
    fn arc(points: &mut Vec<Point>, cx: f64, cy: f64, r: f64, a0: f64, a1: f64) {
        const SEGS: usize = 8;
        for i in 0..=SEGS {
            let a = a0 + (a1 - a0) * i as f64 / SEGS as f64;
            points.push(Self::pt(cx + r * a.cos(), cy - r * a.sin()));
        }
    }

    /// Horizontal bar: square at the left baseline, 4px-rounded data end (right).
    pub fn bar_right_rounded(&self, x: f64, y: f64, w: f64, h: f64, color: Rgb01) {
        let r = crate::pdf::theme::BAR_END_RADIUS.min(w / 2.0).min(h / 2.0);
        self.set_fill(color);
        let mut pts = Vec::with_capacity(24);
        pts.push(Self::pt(x, y));
        pts.push(Self::pt(x + w - r, y));
        Self::arc(&mut pts, x + w - r, y + r, r, std::f64::consts::FRAC_PI_2, 0.0);
        pts.push(Self::pt(x + w, y + h - r));
        Self::arc(&mut pts, x + w - r, y + h - r, r, 0.0, -std::f64::consts::FRAC_PI_2);
        pts.push(Self::pt(x, y + h));
        self.fill_poly(pts);
    }

    /// Column: square at the bottom baseline, 4px-rounded cap (top).
    pub fn column_top_rounded(&self, x: f64, y: f64, w: f64, h: f64, color: Rgb01) {
        let r = crate::pdf::theme::BAR_END_RADIUS.min(w / 2.0).min(h / 2.0);
        self.set_fill(color);
        let mut pts = Vec::with_capacity(24);
        pts.push(Self::pt(x, y + h));
        pts.push(Self::pt(x, y + r));
        Self::arc(&mut pts, x + r, y + r, r, std::f64::consts::PI, std::f64::consts::FRAC_PI_2);
        pts.push(Self::pt(x + w - r, y));
        Self::arc(&mut pts, x + w - r, y + r, r, std::f64::consts::FRAC_PI_2, 0.0);
        pts.push(Self::pt(x + w, y + h));
        self.fill_poly(pts);
    }

    /// Baseline-anchored text. `y` is the text baseline in top-origin pt.
    pub fn text(&self, s: &str, x: f64, y: f64, size: f64, kind: FontKind, color: Rgb01, align: Align) {
        if s.is_empty() {
            return;
        }
        let w = self.fonts.width(s, size, kind);
        let x0 = match align {
            Align::Left => x,
            Align::Right => x - w,
            Align::Center => x - w / 2.0,
        };
        self.set_fill(color);
        let font = match kind {
            FontKind::Regular => &self.fonts.regular,
            FontKind::Bold => &self.fonts.bold,
        };
        self.layer.use_text(
            s,
            size as f32,
            Mm((x0 * PT_TO_MM) as f32),
            Mm(((PAGE_H - y) * PT_TO_MM) as f32),
            font,
        );
    }

    /// Greedy word-wrap; returns the y just below the last line drawn.
    #[allow(clippy::too_many_arguments)]
    pub fn text_wrapped(
        &self,
        s: &str,
        x: f64,
        y: f64,
        max_w: f64,
        size: f64,
        leading: f64,
        kind: FontKind,
        color: Rgb01,
    ) -> f64 {
        let mut line = String::new();
        let mut yy = y;
        for word in s.split_whitespace() {
            let candidate = if line.is_empty() {
                word.to_string()
            } else {
                format!("{line} {word}")
            };
            if self.fonts.width(&candidate, size, kind) > max_w && !line.is_empty() {
                self.text(&line, x, yy, size, kind, color, Align::Left);
                yy += leading;
                line = word.to_string();
            } else {
                line = candidate;
            }
        }
        if !line.is_empty() {
            self.text(&line, x, yy, size, kind, color, Align::Left);
            yy += leading;
        }
        yy
    }
}
