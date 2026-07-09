//! Number/date helpers shared by ingestion and rendering.

/// Group an integer with thousands separators: 1234567 -> "1,234,567".
pub fn group(n: u64) -> String {
    let s = n.to_string();
    let mut out = String::with_capacity(s.len() + s.len() / 3);
    for (i, c) in s.chars().enumerate() {
        if i > 0 && (s.len() - i) % 3 == 0 {
            out.push(',');
        }
        out.push(c);
    }
    out
}

/// Compact figure for stat tiles: below 10,000 grouped, otherwise K/M/B with one decimal.
pub fn compact(n: u64) -> String {
    if n < 10_000 {
        group(n)
    } else if n < 1_000_000 {
        trim_decimal(n as f64 / 1_000.0, "K")
    } else if n < 1_000_000_000 {
        trim_decimal(n as f64 / 1_000_000.0, "M")
    } else {
        trim_decimal(n as f64 / 1_000_000_000.0, "B")
    }
}

fn trim_decimal(v: f64, suffix: &str) -> String {
    let s = format!("{:.1}", v);
    let s = s.strip_suffix(".0").unwrap_or(&s);
    format!("{}{}", s, suffix)
}

/// Human-readable byte size (decimal units, matching manifest size_bytes usage).
pub fn bytes_human(n: u64) -> String {
    const UNITS: [(&str, f64); 4] = [
        ("GB", 1e9),
        ("MB", 1e6),
        ("kB", 1e3),
        ("B", 1.0),
    ];
    for (unit, div) in UNITS {
        if (n as f64) >= div || unit == "B" {
            let v = n as f64 / div;
            return if v >= 100.0 || unit == "B" {
                format!("{:.0} {}", v, unit)
            } else {
                format!("{:.1} {}", v, unit)
            };
        }
    }
    unreachable!()
}

/// Percentage with one decimal, "0%" guarded against a zero denominator.
pub fn pct(num: u64, den: u64) -> String {
    if den == 0 {
        return "0%".to_string();
    }
    let v = num as f64 / den as f64 * 100.0;
    if v > 0.0 && v < 0.1 {
        "<0.1%".to_string()
    } else {
        format!("{:.1}%", v)
    }
}

/// Parse a UTC ISO-8601 timestamp ("2026-07-09T15:19:34Z" or with "+00:00" /
/// fractional seconds) to Unix epoch seconds. Returns None on any other shape;
/// non-UTC offsets are out of scope for bundle artifacts.
pub fn parse_iso8601_utc(s: &str) -> Option<i64> {
    let b = s.as_bytes();
    if b.len() < 19 || b[4] != b'-' || b[7] != b'-' || b[10] != b'T' || b[13] != b':' || b[16] != b':' {
        return None;
    }
    let num = |r: std::ops::Range<usize>| -> Option<i64> { s.get(r)?.parse().ok() };
    let (y, mo, d) = (num(0..4)?, num(5..7)?, num(8..10)?);
    let (h, mi, sec) = (num(11..13)?, num(14..16)?, num(17..19)?);
    let tail = &s[19..];
    let tz_ok = tail == "Z"
        || tail == "+00:00"
        || (tail.starts_with('.')
            && (tail.ends_with('Z') || tail.ends_with("+00:00"))
            && tail[1..tail.len() - if tail.ends_with('Z') { 1 } else { 6 }]
                .chars()
                .all(|c| c.is_ascii_digit()));
    if !tz_ok || !(1..=12).contains(&mo) || !(1..=31).contains(&d) {
        return None;
    }
    Some(days_from_civil(y, mo, d) * 86_400 + h * 3_600 + mi * 60 + sec)
}

/// Howard Hinnant's `days_from_civil` algorithm — days since 1970-01-01.
/// Reference: <https://howardhinnant.github.io/date_algorithms.html#days_from_civil>
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let doy = (153 * (if m > 2 { m - 3 } else { m + 9 }) + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

/// Format epoch seconds as "HH:MM" UTC (for intra-day timeline axes).
pub fn epoch_hhmm(epoch: i64) -> String {
    let secs = epoch.rem_euclid(86_400);
    format!("{:02}:{:02}", secs / 3_600, (secs % 3_600) / 60)
}

/// Format epoch seconds as "YYYY-MM-DD" UTC.
/// Inverse of `days_from_civil` — same reference algorithm (`civil_from_days`).
pub fn epoch_date(epoch: i64) -> String {
    let z = epoch.div_euclid(86_400) + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!("{:04}-{:02}-{:02}", y, m, d)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn grouping() {
        assert_eq!(group(0), "0");
        assert_eq!(group(999), "999");
        assert_eq!(group(1_020_048), "1,020,048");
        assert_eq!(group(94_841), "94,841");
    }

    #[test]
    fn compact_figures() {
        assert_eq!(compact(1_284), "1,284");
        assert_eq!(compact(12_900), "12.9K");
        assert_eq!(compact(94_841), "94.8K");
        assert_eq!(compact(1_020_048), "1M");
        assert_eq!(compact(776_716), "776.7K");
    }

    #[test]
    fn bytes_humanized() {
        assert_eq!(bytes_human(357), "357 B");
        assert_eq!(bytes_human(22_269_565), "22.3 MB");
        assert_eq!(bytes_human(5_158_871_335), "5.2 GB");
    }

    #[test]
    fn percentages() {
        assert_eq!(pct(28_581, 49_569), "57.7%");
        assert_eq!(pct(0, 0), "0%");
        assert_eq!(pct(24, 776_716), "<0.1%");
    }

    #[test]
    fn iso8601() {
        // 2026-07-09T15:19:34Z == 1783610374 (verified against `date -ud ... +%s`)
        assert_eq!(parse_iso8601_utc("2026-07-09T15:19:34Z"), Some(1_783_610_374));
        assert_eq!(parse_iso8601_utc("1970-01-01T00:00:00Z"), Some(0));
        assert_eq!(parse_iso8601_utc("2026-07-09T15:26:51+00:00"), Some(1_783_610_811));
        assert_eq!(
            parse_iso8601_utc("2026-07-09T17:01:36.248649+00:00"),
            Some(1_783_616_496)
        );
        assert_eq!(parse_iso8601_utc("not a date"), None);
        assert_eq!(parse_iso8601_utc("2026-07-09T15:19:34-03:00"), None);
    }

    #[test]
    fn hhmm() {
        assert_eq!(epoch_hhmm(1_783_610_374), "15:19");
        assert_eq!(epoch_hhmm(0), "00:00");
    }

    #[test]
    fn dates() {
        assert_eq!(epoch_date(0), "1970-01-01");
        assert_eq!(epoch_date(1_783_610_374), "2026-07-09");
    }
}
