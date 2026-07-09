//! Chart components: horizontal bars, columns, grouped bars, stat tiles,
//! legends. Mark specs follow the dataviz method: thin marks, rounded data
//! ends (square at the baseline), 2px surface gaps, hairline recessive grid,
//! selective ink-colored direct labels (text never wears the series color).

use crate::pdf::canvas::{Align, Canvas, FontKind};
use crate::pdf::theme::*;
use crate::util;

/// "Nice" axis ticks: 0..=max in 1/2/2.5/5 × 10^k steps, 4–6 ticks.
pub fn nice_ticks(max: f64) -> Vec<f64> {
    if max <= 0.0 {
        return vec![0.0, 1.0];
    }
    let raw_step = max / 4.0;
    let mag = 10f64.powf(raw_step.log10().floor());
    let step = [1.0, 2.0, 2.5, 5.0, 10.0]
        .iter()
        .map(|m| m * mag)
        .find(|&s| max / s <= 5.0)
        .unwrap_or(10.0 * mag);
    let n = (max / step).ceil() as usize;
    (0..=n).map(|i| i as f64 * step).collect()
}

pub fn fmt_tick(v: f64) -> String {
    if v >= 1_000_000.0 {
        format!("{}M", trim(v / 1_000_000.0))
    } else if v >= 1_000.0 {
        format!("{}K", trim(v / 1_000.0))
    } else {
        trim(v)
    }
}

fn trim(v: f64) -> String {
    let s = format!("{:.1}", v);
    s.strip_suffix(".0").unwrap_or(&s).to_string()
}

pub struct HBarRow {
    pub label: String,
    pub value: f64,
    pub value_label: String,
    pub color: Rgb01,
}

pub struct HBarChart {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub label_w: f64,
    pub rows: Vec<HBarRow>,
    /// Extra note rendered small under the row label, e.g. a share "42.1%".
    pub row_gap: f64,
}

impl HBarChart {
    pub fn height(&self) -> f64 {
        self.rows.len() as f64 * (BAR_THICKNESS + self.row_gap) + 14.0
    }

    pub fn draw(&self, c: &Canvas) {
        let plot_x = self.x + self.label_w + 8.0;
        let plot_w = self.w - self.label_w - 8.0 - 46.0; // room for tip labels
        let max = self.rows.iter().map(|r| r.value).fold(0.0, f64::max);
        let ticks = nice_ticks(max);
        let tick_max = *ticks.last().unwrap();
        let rows_h = self.rows.len() as f64 * (BAR_THICKNESS + self.row_gap) - self.row_gap;

        // Recessive grid behind the marks, hairline, solid.
        for &t in &ticks {
            let gx = plot_x + t / tick_max * plot_w;
            c.vline(gx, self.y, self.y + rows_h, GRIDLINE, HAIRLINE);
            c.text(
                &fmt_tick(t),
                gx,
                self.y + rows_h + 11.0,
                6.5,
                FontKind::Regular,
                INK_MUTED,
                Align::Center,
            );
        }

        for (i, row) in self.rows.iter().enumerate() {
            let ry = self.y + i as f64 * (BAR_THICKNESS + self.row_gap);
            let bw = (row.value / tick_max * plot_w).max(HAIRLINE);
            c.text(
                &row.label,
                plot_x - 8.0,
                ry + BAR_THICKNESS - 3.0,
                7.5,
                FontKind::Regular,
                INK_SECONDARY,
                Align::Right,
            );
            c.bar_right_rounded(plot_x, ry, bw, BAR_THICKNESS, row.color);
            c.text(
                &row.value_label,
                plot_x + bw + 4.0,
                ry + BAR_THICKNESS - 3.0,
                7.0,
                FontKind::Regular,
                INK,
                Align::Left,
            );
        }
        // Baseline on top of the grid.
        c.vline(plot_x, self.y, self.y + rows_h, BASELINE, 1.0);
    }
}

pub struct ColumnChart {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
    pub values: Vec<f64>,
    pub color: Rgb01,
    /// Sparse x labels: (index, text).
    pub x_labels: Vec<(usize, String)>,
    pub y_label: String,
}

impl ColumnChart {
    pub fn draw(&self, c: &Canvas) {
        if self.values.is_empty() {
            return;
        }
        let max = self.values.iter().copied().fold(0.0, f64::max);
        let ticks = nice_ticks(max);
        let tick_max = *ticks.last().unwrap();
        let plot_h = self.h - 16.0;

        for &t in &ticks {
            let gy = self.y + plot_h - t / tick_max * plot_h;
            c.hline(self.x, self.x + self.w, gy, GRIDLINE, HAIRLINE);
            c.text(
                &fmt_tick(t),
                self.x - 4.0,
                gy + 2.2,
                6.5,
                FontKind::Regular,
                INK_MUTED,
                Align::Right,
            );
        }

        let n = self.values.len() as f64;
        let slot = self.w / n;
        let cw = (slot - SURFACE_GAP).max(0.5).min(18.0); // ≤24px thick
        let (max_i, _) = self
            .values
            .iter()
            .enumerate()
            .fold((0usize, 0.0f64), |acc, (i, &v)| if v > acc.1 { (i, v) } else { acc });

        for (i, &v) in self.values.iter().enumerate() {
            if v <= 0.0 {
                continue;
            }
            let cx = self.x + i as f64 * slot + (slot - cw) / 2.0;
            let ch = (v / tick_max * plot_h).max(HAIRLINE);
            c.column_top_rounded(cx, self.y + plot_h - ch, cw, ch, self.color);
        }
        // Selective direct label: the peak only.
        let peak = self.values[max_i];
        if peak > 0.0 {
            let cx = self.x + max_i as f64 * slot + slot / 2.0;
            let cy = self.y + plot_h - peak / tick_max * plot_h;
            c.text(
                &util::group(peak as u64),
                cx,
                cy - 3.0,
                6.5,
                FontKind::Bold,
                INK,
                Align::Center,
            );
        }

        c.hline(self.x, self.x + self.w, self.y + plot_h, BASELINE, 1.0);
        for (i, label) in &self.x_labels {
            let cx = self.x + *i as f64 * slot + slot / 2.0;
            c.text(label, cx, self.y + plot_h + 10.0, 6.5, FontKind::Regular, INK_MUTED, Align::Center);
        }
        if !self.y_label.is_empty() {
            c.text(
                &self.y_label,
                self.x,
                self.y - 6.0,
                7.0,
                FontKind::Regular,
                INK_MUTED,
                Align::Left,
            );
        }
    }
}

/// Two-series grouped horizontal bars (e.g. two rates per language).
pub struct GroupedHBarChart {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub label_w: f64,
    pub max_value: f64,
    /// (label, series-a value, series-b value), values already in axis units.
    pub rows: Vec<(String, f64, f64)>,
    pub colors: [Rgb01; 2],
    pub value_fmt: fn(f64) -> String,
}

const GROUP_BAR: f64 = 8.0;

impl GroupedHBarChart {
    pub fn draw(&self, c: &Canvas) {
        let plot_x = self.x + self.label_w + 8.0;
        let plot_w = self.w - self.label_w - 8.0 - 46.0;
        let ticks = nice_ticks(self.max_value);
        let tick_max = *ticks.last().unwrap();
        let band = GROUP_BAR * 2.0 + SURFACE_GAP + 10.0;
        let rows_h = self.rows.len() as f64 * band - 10.0;

        for &t in &ticks {
            let gx = plot_x + t / tick_max * plot_w;
            c.vline(gx, self.y, self.y + rows_h, GRIDLINE, HAIRLINE);
            c.text(
                &format!("{}%", fmt_tick(t)),
                gx,
                self.y + rows_h + 11.0,
                6.5,
                FontKind::Regular,
                INK_MUTED,
                Align::Center,
            );
        }

        for (i, (label, a, b)) in self.rows.iter().enumerate() {
            let ry = self.y + i as f64 * band;
            c.text(
                label,
                plot_x - 8.0,
                ry + GROUP_BAR * 2.0 - 5.0,
                7.5,
                FontKind::Regular,
                INK_SECONDARY,
                Align::Right,
            );
            for (j, (&v, &color)) in [a, b].into_iter().zip(&self.colors).enumerate() {
                let by = ry + j as f64 * (GROUP_BAR + SURFACE_GAP);
                let bw = (v / tick_max * plot_w).max(HAIRLINE);
                c.bar_right_rounded(plot_x, by, bw, GROUP_BAR, color);
                c.text(
                    &(self.value_fmt)(v),
                    plot_x + bw + 4.0,
                    by + GROUP_BAR - 2.0,
                    6.5,
                    FontKind::Regular,
                    INK,
                    Align::Left,
                );
            }
        }
        c.vline(plot_x, self.y, self.y + rows_h, BASELINE, 1.0);
    }
}

/// Legend row: colored swatch + ink label per series.
pub fn legend(c: &Canvas, x: f64, y: f64, entries: &[(&str, Rgb01)]) {
    let mut cx = x;
    for (label, color) in entries {
        c.rect(cx, y - 5.5, 7.0, 7.0, *color);
        c.text(label, cx + 10.0, y, 7.5, FontKind::Regular, INK_SECONDARY, Align::Left);
        cx += 10.0 + c.fonts.width(label, 7.5, FontKind::Regular) + 16.0;
    }
}

/// Stat tile: label / big proportional value / optional muted note.
/// The value shrinks to fit the tile; the note is ellipsized — text never
/// overflows its own tile.
pub fn stat_tile(c: &Canvas, x: f64, y: f64, w: f64, h: f64, label: &str, value: &str, note: &str) {
    let inner = w - 20.0;
    c.rect_outline(x, y, w, h, HAIRLINE_BORDER, HAIRLINE);
    c.text(label, x + 10.0, y + 16.0, 7.5, FontKind::Regular, INK_SECONDARY, Align::Left);
    let mut size = 20.0;
    while size > 8.0 && c.fonts.width(value, size, FontKind::Bold) > inner {
        size -= 0.5;
    }
    c.text(value, x + 10.0, y + 40.0, size, FontKind::Bold, INK, Align::Left);
    if !note.is_empty() {
        let mut note = note.to_string();
        if c.fonts.width(&note, 6.8, FontKind::Regular) > inner {
            while !note.is_empty() && c.fonts.width(&format!("{note}…"), 6.8, FontKind::Regular) > inner {
                note.pop();
            }
            note.push('…');
        }
        c.text(&note, x + 10.0, y + h - 9.0, 6.8, FontKind::Regular, INK_MUTED, Align::Left);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ticks_are_clean() {
        assert_eq!(nice_ticks(50_012.0).last().copied(), Some(60_000.0));
        assert_eq!(nice_ticks(97.0), vec![0.0, 20.0, 40.0, 60.0, 80.0, 100.0]);
        assert_eq!(nice_ticks(0.0), vec![0.0, 1.0]);
        let t = nice_ticks(1.0);
        assert!(t.len() >= 2 && t.len() <= 6);
    }

    #[test]
    fn tick_formatting() {
        assert_eq!(fmt_tick(0.0), "0");
        assert_eq!(fmt_tick(2_500.0), "2.5K");
        assert_eq!(fmt_tick(1_000_000.0), "1M");
        assert_eq!(fmt_tick(60_000.0), "60K");
    }
}
