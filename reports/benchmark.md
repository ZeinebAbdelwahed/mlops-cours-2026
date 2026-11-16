# Benchmark de latence — german-credit, modèle v13

1000 appels par backend, après 10 appels de chauffe · x86_64 · Python 3.12.15

| Backend | P50 (ms) | P95 (ms) | P99 (ms) |
|---|---|---|---|
| sklearn_pipeline | 3.055 | 3.958 | 4.700 |
| onnx_python | 0.057 | 0.073 | 0.085 |
| onnx_batch_100 | 0.235 | 0.287 | 0.374 |

Accélération ONNX vs scikit-learn (P50, 1 ligne) : x53.9
