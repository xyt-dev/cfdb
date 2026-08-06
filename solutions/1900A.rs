// 1900A Cover in Water — 我的解答
use std::io::{self, BufWriter, Read, Write};

fn main() {
    let mut input = String::new();
    io::stdin().read_to_string(&mut input).unwrap();
    let mut it = input.split_whitespace();
    let mut next = || it.next().unwrap();
    let mut out = BufWriter::new(io::stdout().lock());

    let t: usize = next().parse().unwrap();
    for _ in 0..t {
        let n: usize = next().parse().unwrap();
        let s: Vec<char> = next().chars().collect();
        let empty = s.iter().filter(|&&c| c == '.').count();
        let mut three = false;
        for w in s.windows(3) {
            if w == ['.', '.', '.'] { three = true; break; }
        }
        writeln!(out, "{}", if three { 2 } else { empty }).unwrap();
    }
}
