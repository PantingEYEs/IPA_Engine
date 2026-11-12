# Existing graph operator audit

This is a read-only inventory of the exact IPA Board assets, parsed with official `onnx==1.18.0`. The three model chunks, reconstructed model and tokenizer match the sizes and SHA-256 hashes in the application asset manifest. No graph, model, compiled library or AAR was changed.

| Graph | Inference nodes, including subgraphs | Required operator domains | Local functions |
| --- | ---: | --- | ---: |
| `tokenizer.onnx` | 1 | `ai.onnx.contrib`: `SentencepieceTokenizer` | 0 |
| Reconstructed E5 model | 1630 | Standard default ONNX domain only (21 operator types) | 0 |

The only Extensions custom operator required by either existing graph is `ai.onnx.contrib::SentencepieceTokenizer`. The selected SPM profile includes it; its additional `SentencepieceDecoder` is not used by these graphs. E5 does not reference any Extensions custom operator, and the ONNX Runtime 1.23.2 main library has not been reduced. Both models have zero training-info entries and no external tensors.

## E5 node inventory

All entries below use the standard default ONNX domain.

| Operator | Nodes |
| --- | ---: |
| `Add` | 172 |
| `Cast` | 97 |
| `Concat` | 48 |
| `Constant` | 284 |
| `DequantizeLinear` | 3 |
| `Div` | 49 |
| `DynamicQuantizeLinear` | 96 |
| `Erf` | 12 |
| `Gather` | 100 |
| `MatMulInteger` | 96 |
| `Mul` | 242 |
| `Pow` | 25 |
| `ReduceMean` | 50 |
| `Reshape` | 48 |
| `Shape` | 97 |
| `Slice` | 1 |
| `Softmax` | 12 |
| `Sqrt` | 25 |
| `Sub` | 26 |
| `Transpose` | 48 |
| `Unsqueeze` | 99 |

The complete imports, hashes, part inventory and parser versions are in [graph-operator-inventory.json](graph-operator-inventory.json). This static dependency check complements runtime tests; it does not replace execution testing or establish compatibility for a future replacement graph.
