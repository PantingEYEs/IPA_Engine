//! Streaming minimal DAFSA construction; wire output matches the previous builder.
use std::collections::HashMap;
use std::fs::File;
use std::io::{self, BufReader, BufWriter, Read, Write};

#[derive(Default)]
struct Node {
    final_node: bool,
    arcs: Vec<(u8, u32)>,
}

struct Builder {
    nodes: Vec<Node>,
    counts: Vec<u32>,
    registry: HashMap<Vec<u8>, u32>,
    path: Vec<Node>,
    bits: u32,
}

impl Builder {
    fn freeze(&mut self, node: Node) -> Result<u32, String> {
        let mut signature = Vec::with_capacity(1 + node.arcs.len() * 5);
        signature.push(u8::from(node.final_node));
        let mut count = u64::from(node.final_node);
        for &(label, child) in &node.arcs {
            let suffix = self.counts.get(child as usize).ok_or("unfrozen child")?;
            signature.push(label);
            signature.extend_from_slice(&child.to_le_bytes());
            count += u64::from(suffix & 0x7fff_ffff);
        }
        if let Some(&id) = self.registry.get(&signature) {
            return Ok(id);
        }
        if count > 0x7fff_ffff {
            return Err("accepted suffix count exceeds 31 bits".into());
        }
        let limit = if self.bits == 24 { 0x100_0000 } else { u32::MAX as usize };
        if self.nodes.len() >= limit {
            return Err("node count exceeds index target width".into());
        }
        let id = self.nodes.len() as u32;
        self.registry.insert(signature, id);
        self.counts.push(count as u32 | if node.final_node { 0x8000_0000 } else { 0 });
        self.nodes.push(node);
        Ok(id)
    }

    fn freeze_to(&mut self, prefix: usize) -> Result<(), String> {
        while self.path.len() > prefix + 1 {
            let node = self.path.pop().ok_or("missing path")?;
            let id = self.freeze(node)?;
            self.path.last_mut().ok_or("missing parent")?.arcs.last_mut().ok_or("missing arc")?.1 = id;
        }
        Ok(())
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let args: Vec<_> = std::env::args_os().collect();
    if !(args.len() == 3 || args.len() == 4) {
        return Err("usage: build_dawg KEY_STREAM GRAPH_SECTIONS [24|32]".into());
    }
    let bits = if args.len() == 4 { args[3].to_str().ok_or("invalid width")?.parse::<u32>()? } else { 24 };
    if bits != 24 && bits != 32 { return Err("index width must be 24 or 32".into()); }
    let mut input = BufReader::new(File::open(&args[1])?);
    let mut builder = Builder { nodes: Vec::new(), counts: Vec::new(), registry: HashMap::new(),
                                path: vec![Node::default()], bits };
    let mut previous = Vec::<u8>::new();
    let mut trie_nodes = 1u64;
    let mut keys = 0u32;
    loop {
        let mut length = [0u8; 2];
        // Distinguish a clean EOF from a truncated length, including interrupted reads.
        match input.read(&mut length[..1]) {
            Ok(0) => break,
            Ok(_) => (),
            Err(e) if e.kind() == io::ErrorKind::Interrupted => continue,
            Err(e) => return Err(e.into()),
        }
        input.read_exact(&mut length[1..])?;
        let length = u16::from_le_bytes(length) as usize;
        if length == 0 || length > 64 { return Err("key scalar length outside 1..64".into()); }
        let mut key = vec![0; length];
        input.read_exact(&mut key)?;
        if key.contains(&0) { return Err("zero key label".into()); }
        if keys > 0 && previous >= key { return Err("keys must be unique and sorted by labels".into()); }
        let shared = previous.iter().zip(&key).take_while(|(a,b)| a == b).count();
        builder.freeze_to(shared)?;
        for &label in &key[shared..] {
            builder.path.last_mut().ok_or("missing path")?.arcs.push((label, u32::MAX));
            builder.path.push(Node::default());
        }
        builder.path.last_mut().ok_or("missing terminal")?.final_node = true;
        trie_nodes += (key.len() - shared) as u64;
        if keys == 0x7fff_ffff { return Err("too many keys".into()); }
        keys += 1;
        previous = key;
    }
    builder.freeze_to(0)?;
    let root_node = builder.path.pop().ok_or("missing root")?;
    let root = builder.freeze(root_node)?;
    let arcs: u64 = builder.nodes.iter().map(|n| n.arcs.len() as u64).sum();
    if arcs > u32::MAX as u64 { return Err("arc count exceeds uint32".into()); }
    let mut output = BufWriter::new(File::create(&args[2])?);
    for value in [builder.nodes.len() as u32, arcs as u32, keys, root] {
        output.write_all(&value.to_le_bytes())?;
    }
    output.write_all(&trie_nodes.to_le_bytes())?;
    let mut offset = 0u32;
    for node in &builder.nodes {
        output.write_all(&offset.to_le_bytes())?;
        offset += node.arcs.len() as u32;
    }
    output.write_all(&offset.to_le_bytes())?;
    for value in &builder.counts { output.write_all(&value.to_le_bytes())?; }
    for (id,node) in builder.nodes.iter().enumerate() {
        for &(label,child) in &node.arcs {
            if child as usize >= id { return Err("child must precede parent".into()); }
            output.write_all(&[label])?;
            output.write_all(&child.to_le_bytes()[..(bits / 8) as usize])?;
        }
    }
    output.flush()?;
    println!("{{\"nodes\":{},\"arcs\":{},\"keys\":{},\"root\":{},\"trie_nodes\":{},\"trie_arcs\":{}}}",
             builder.nodes.len(), arcs, keys, root, trie_nodes, trie_nodes - 1);
    Ok(())
}

fn main() {
    if let Err(error) = run() {
        eprintln!("build_dawg: {error}");
        std::process::exit(1);
    }
}
