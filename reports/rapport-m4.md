# Rapport — Lab 4 : packaging du modèle

Binôme : Zeineb / Mohammed · Dataset : german-credit (`name` de dataset.yaml) · Modèle : `scoring-model` v13 (régression logistique, seuil de décision 0,318)

## 1. Comparatif des tailles

| Image | Taille compressée (`docker image inspect`) | Taille `docker images` | CVE HIGH + CRITICAL (Trivy) | Utilisateur |
|---|---|---|---|---|
| `scoring-api:naive` | 583 Mo | 2,18 Go | 427 (HIGH 412, CRITICAL 15) | root |
| `scoring-api:optimized` | **125 Mo** | **535 Mo** | **49 (HIGH 49, CRITICAL 0)** | ml (uid 10001) |
| `scoring-api:bentoml` | 358 Mo | 1,41 Go | 143 (HIGH 143, CRITICAL 0) | bentoml |

Note sur la mesure : Docker Desktop utilise ici le *containerd image store*. `docker images` additionne les couches compressées et décompressées, ce qui gonfle les chiffres par rapport à l'énoncé ; `docker image inspect` (mesure utilisée par `make check-m4`) donne la taille compressée, celle qui transite vers le registry. Taille décompressée de l'image optimisée d'après `docker history` : ≈ 410 Mo (base Debian slim ≈ 134 Mo + dépendances Python 276 Mo + code et modèle ≈ 0,1 Mo). Quelle que soit la mesure, l'image optimisée est 3 à 5 fois plus petite que la naïve.

## 2. Comparatif des latences

1 000 appels par backend après 10 appels de chauffe (`packaging/app/benchmark.py`, détail dans `reports/benchmark.md`), même machine (WSL2, Python 3.12).

| Backend | P50 (ms) | P95 (ms) | P99 (ms) |
|---|---|---|---|
| sklearn_pipeline | 3,055 | 3,958 | 4,700 |
| onnx_python | 0,057 | 0,073 | 0,085 |
| onnx_batch_100 | 0,235 | 0,287 | 0,374 |

Accélération ONNX vs scikit-learn (P50, 1 ligne) : **×53,9**.

Test de charge Locust sur l'image optimisée (100 utilisateurs, 30 s) : 38 675 requêtes, **0 échec**, ≈ 1 350 req/s en moyenne (pic ≈ 1 550 req/s) ; latence de bout en bout P50 33 ms, P95 61 ms, P99 78 ms.

## 3. Interprétation

**Pourquoi l'image optimisée est-elle plus petite ?**
- Base `python:3.12-slim` au lieu de `python:3.12` : pas de compilateurs ni d'outils Debian inutiles (87 paquets Debian contre 469).
- Build multi-stage : les dépendances sont installées dans un stage `builder` ; seul le dossier `/deps` est copié dans l'image finale, sans pip ni son cache (`--no-cache-dir`).
- Dépendances minimales : l'image n'exécute que le graphe ONNX, elle n'embarque ni MLflow, ni scikit-learn, ni skl2onnx.
- `.dockerignore` en liste blanche : seuls 8 fichiers sont envoyés au build (contexte de 758 octets contre 44 Mo pour la naïve). Effet de bord majeur : l'image naïve, construite avec `COPY . /app`, contenait le `.env` avec les identifiants S3 (vérifié avec `docker run scoring-api:naive cat .env`) et tournait en root ; l'image optimisée ne contient aucun secret et tourne avec un utilisateur non-root à UID fixe.

**Pourquoi ONNX est-il plus rapide que scikit-learn ?**
- Pour une seule ligne, le temps de scikit-learn est dominé par son surcoût fixe : validation du DataFrame, sélection des colonnes par leur nom dans le `ColumnTransformer`, un appel Python par transformateur du pipeline. Le calcul de la régression logistique elle-même est négligeable.
- ONNX Runtime exécute le graphe complet (imputation, standardisation, one-hot, modèle) en une seule passe de code natif compilé, sans aller-retour Python entre les étapes.
- Le traitement par lot est très rentable : 100 lignes coûtent seulement ≈ 4 fois une ligne (≈ 0,002 ms par prédiction).
- Sous charge, la latence de l'API (≈ 33 ms) est due à la file d'attente devant l'unique worker uvicorn, pas au modèle (0,06 ms) : c'est un problème de capacité, que l'autoscaling du Lab 5 permettra d'absorber.
- La conversion ne change pas les prédictions : écart maximal de 8,7·10⁻⁸ sur les 200 lignes de test et 100 % de décisions identiques au seuil (`verify_onnx.py`).

**Que montrent les scans ?**
- Trivy : la surface d'attaque suit le nombre de paquets embarqués. 427 CVE dont 15 critiques pour la naïve, 143 pour BentoML (même base slim, mais BentoML, scikit-learn, SciPy et uv en plus), 49 sans aucune critique pour l'optimisée.
- ModelScan sur le modèle ONNX : « No issues found », mais le fichier est en réalité ignoré (skipped). ONNX est sûr par construction (graphe protobuf déclaratif, sans code), pas parce qu'il a été analysé.
- ModelScan sur les pickles : le pickle piégé est détecté (CRITICAL, opérateur `system` du module `posix`), le pickle sain passe. Charger un pickle revient à exécuter du code : un fichier modèle d'origine non maîtrisée est un vecteur d'attaque.
- Modèle du Registry (format skops de MLflow 3) : skops ne demande à instancier qu'un seul type hors de sa liste de confiance, `numpy.dtype`, autorisé explicitement au Lab 1. ModelScan 0.8.8 a par ailleurs levé des erreurs `numpy.lib.format has no attribute '_check_version'` sur les tableaux du fichier skops : c'est une incompatibilité de l'outil avec numpy 2.5, pas une faille détectée, mais cela veut dire que ces tableaux n'ont pas été analysés par ce scanner.
- Le modèle de l'image BentoML est un pickle (`saved_model.pkl` écrit par joblib), contrairement à l'image optimisée.

## 4. Choix recommandé

**`scoring-api:optimized` (FastAPI + ONNX Runtime) pour la production.** Elle est la meilleure sur tous les critères mesurés : la plus petite (125 Mo compressés, donc des pods qui démarrent vite lors de l'autoscaling), la plus rapide (×54 sur la latence d'inférence), la plus sûre (49 CVE, aucune critique, non-root, aucun secret, modèle ONNX sans code exécutable) et entièrement traçable (labels `mlflow.model_version`, `metadata.json` exposé sur `/metadata`). Son contrat d'API (422 sur requête invalide, schéma généré depuis `dataset.yaml`) est celui sur lequel s'appuient les labs suivants.

BentoML reste intéressant pour prototyper vite ou pour des modèles non convertibles en ONNX (il gère lui-même le stockage du modèle et la construction de l'image), mais ici il est plus gros, plus exposé (143 CVE), sert un pickle et répond 400 au lieu de 422 sur une requête invalide. L'image naïve n'est qu'une baseline : elle ne doit jamais être poussée (secrets embarqués, root).

## 5. Pistes d'amélioration

- **Image distroless ou Chainguard** (`gcr.io/distroless/python3`) : supprimer shell et gestionnaire de paquets réduirait encore la taille et le nombre de CVE.
- **Alléger les dépendances** : `/deps` pèse 276 Mo. Retirer pandas de l'image (utilisé seulement pour lire `dataset.yaml` via `dataset_config.py`) et installer `uvicorn` sans l'extra `[standard]` ferait gagner plusieurs dizaines de Mo.
- **Quantization INT8** (`onnxruntime.quantization`) : utile pour de gros modèles (forêts, réseaux) ; pour une régression logistique de 11 ko, le gain serait négligeable.
- **Plusieurs workers uvicorn** ou plusieurs réplicas (autoscaling du Lab 5) : le test Locust montre que la limite est la file d'attente, pas le modèle.
- **Scan dans la CI** (Module 6) : bloquer le merge si Trivy trouve une CVE CRITICAL, et mettre à jour régulièrement l'image de base, la plupart des CVE ayant un correctif côté Debian.
- **Préchargement du modèle par un Init Container** : permettrait de changer de modèle sans reconstruire l'image, mais au prix de l'immuabilité (une image = une version du modèle) et de la dépendance à MLflow au démarrage ; à réserver aux modèles très lourds.
