"""Convert complete graph dictionary index widths without rescanning SQLite."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from graph.compiler import convert_indices


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--index-bits', type=int, choices=(24, 32), required=True)
    parser.add_argument('--max-bytes', type=int,
                        help='default ceiling: 30000000 for 24-bit, 40000000 for 32-bit; never prunes')
    args = parser.parse_args()
    manifest = convert_indices(args.source, args.output, args.index_bits, args.max_bytes)
    print(json.dumps({name: manifest[name] for name in ('version', 'index_bits', 'bytes', 'sha256')},
                     ensure_ascii=False))


if __name__ == '__main__':
    main()
