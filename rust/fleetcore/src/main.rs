// fleetcore: read every *.json in a board's msgs/ directory in parallel and print one JSON array
// [[file_name, size, mtime_ns, text], ...] sorted by name. fastboard.py uses it for cold loads
// (no cache, or many new files); measured 2x faster than sequential Python on 4,315 files.
use rayon::prelude::*;
use std::{env, fs, io::Write, path::PathBuf, time::UNIX_EPOCH};

fn main() {
    let dir = PathBuf::from(env::args().nth(1).expect("usage: fleetcore <msgs-dir>"));
    let mut names: Vec<String> = fs::read_dir(&dir)
        .expect("read_dir")
        .filter_map(|e| e.ok())
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .filter(|n| n.ends_with(".json"))
        .collect();
    names.sort();
    let rows: Vec<serde_json::Value> = names
        .par_iter()
        .filter_map(|n| {
            let p = dir.join(n);
            let meta = fs::metadata(&p).ok()?;
            let text = fs::read_to_string(&p).ok()?;
            let mtime = meta.modified().ok()?.duration_since(UNIX_EPOCH).ok()?.as_nanos() as u64;
            Some(serde_json::json!([n, meta.len(), mtime, text]))
        })
        .collect();
    let out = std::io::stdout();
    let mut out = out.lock();
    serde_json::to_writer(&mut out, &rows).unwrap();
    out.flush().unwrap();
}
