//! Incremental extraction of top-level JSON objects from the `@graph` array of
//! a JSON-LD document far too large to parse whole.
//!
//! The splitter is a byte-level state machine (string/escape aware), fed with
//! sequential blocks of the file. It yields the byte range of each complete
//! top-level object; partial objects at block boundaries are carried over.

/// States persist across `feed` calls so objects may span block boundaries.
pub struct GraphSplitter {
    /// Bytes not yet fully consumed (tail of a partial object, or pre-@graph preamble).
    buf: Vec<u8>,
    /// Whether we've located the start of the @graph array yet.
    in_graph: bool,
    /// Whether the @graph array has closed; everything after is epilogue.
    done: bool,
    /// Current object nesting depth (0 = between array items).
    depth: u32,
    in_string: bool,
    escaped: bool,
    /// Byte offset in `buf` where the current object started.
    obj_start: usize,
    /// Scan resume position in `buf`.
    scan_pos: usize,
}

impl Default for GraphSplitter {
    fn default() -> Self {
        Self::new()
    }
}

impl GraphSplitter {
    pub fn new() -> Self {
        Self {
            buf: Vec::new(),
            in_graph: false,
            done: false,
            depth: 0,
            in_string: false,
            escaped: false,
            obj_start: 0,
            scan_pos: 0,
        }
    }

    /// Append a block and return the complete objects found so far.
    /// Each returned Vec<u8> is the exact bytes of one `@graph` item.
    pub fn feed(&mut self, block: &[u8]) -> Vec<Vec<u8>> {
        if self.done {
            return Vec::new();
        }
        self.buf.extend_from_slice(block);

        if !self.in_graph {
            // Locate `"@graph"` then the `[` that follows. The preamble
            // (@context) contains no such key, and JSON keys are unique here.
            const NEEDLE: &[u8] = b"\"@graph\"";
            if let Some(pos) = find(&self.buf, NEEDLE) {
                if let Some(bracket) = self.buf[pos + NEEDLE.len()..]
                    .iter()
                    .position(|&c| c == b'[')
                {
                    let start = pos + NEEDLE.len() + bracket + 1;
                    self.buf.drain(..start);
                    self.in_graph = true;
                    self.scan_pos = 0;
                } else {
                    return Vec::new(); // `[` not in this block yet
                }
            } else {
                // Keep only a needle-sized tail; the preamble is small anyway.
                let keep = self.buf.len().saturating_sub(NEEDLE.len());
                self.buf.drain(..keep);
                return Vec::new();
            }
        }

        let mut items = Vec::new();
        let mut i = self.scan_pos;
        while i < self.buf.len() {
            let c = self.buf[i];
            if self.in_string {
                if self.escaped {
                    self.escaped = false;
                } else if c == b'\\' {
                    self.escaped = true;
                } else if c == b'"' {
                    self.in_string = false;
                }
            } else {
                match c {
                    b'"' => self.in_string = true,
                    b'{' => {
                        if self.depth == 0 {
                            self.obj_start = i;
                        }
                        self.depth += 1;
                    }
                    b'}' if self.depth > 0 => {
                        self.depth -= 1;
                        if self.depth == 0 {
                            items.push(self.buf[self.obj_start..=i].to_vec());
                        }
                    }
                    b']' if self.depth == 0 => {
                        // End of @graph — ignore the document epilogue.
                        self.done = true;
                        self.buf.clear();
                        self.scan_pos = 0;
                        return items;
                    }
                    _ => {}
                }
            }
            i += 1;
        }

        // Drop consumed bytes: everything before the current partial object
        // (or everything, when between items).
        let keep_from = if self.depth > 0 { self.obj_start } else { i };
        self.buf.drain(..keep_from);
        self.scan_pos = self.buf.len();
        self.obj_start = 0;
        items
    }
}

fn find(haystack: &[u8], needle: &[u8]) -> Option<usize> {
    haystack.windows(needle.len()).position(|w| w == needle)
}

#[cfg(test)]
mod tests {
    use super::*;

    const DOC: &str = r#"{
      "@context": {"cbm": "https://example.org/cbm#"},
      "@graph": [
        {"@id": "a", "n": 1},
        {"@id": "b", "nested": {"deep": {"x": "}"}}, "s": "brace { in string"},
        {"@id": "c", "esc": "quote \" and backslash \\", "arr": [1, 2, {"y": 3}]}
      ]
    }"#;

    fn collect(doc: &str, block_size: usize) -> Vec<String> {
        let mut sp = GraphSplitter::new();
        let mut out = Vec::new();
        for chunk in doc.as_bytes().chunks(block_size) {
            for item in sp.feed(chunk) {
                out.push(String::from_utf8(item).unwrap());
            }
        }
        out
    }

    #[test]
    fn whole_doc_single_block() {
        let items = collect(DOC, DOC.len());
        assert_eq!(items.len(), 3);
        let v: serde_json::Value = serde_json::from_str(&items[1]).unwrap();
        assert_eq!(v["s"], "brace { in string");
        assert_eq!(v["nested"]["deep"]["x"], "}");
    }

    #[test]
    fn boundary_at_every_byte() {
        // Objects must survive any block boundary, including mid-escape.
        for bs in 1..=13 {
            let items = collect(DOC, bs);
            assert_eq!(items.len(), 3, "block size {bs}");
            for it in &items {
                serde_json::from_str::<serde_json::Value>(it).unwrap();
            }
        }
    }

    #[test]
    fn stops_at_graph_close() {
        let doc = r#"{"@graph": [{"a": 1}], "after": {"not": "an item"}}"#;
        let items = collect(doc, 7);
        assert_eq!(items.len(), 1);
        assert_eq!(items[0], r#"{"a": 1}"#);
    }
}
