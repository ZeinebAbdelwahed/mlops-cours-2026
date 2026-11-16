# Rapport — Lab 6 : CI/CD/CT de bout en bout

Binôme : projet individuel (Zeineb Abdelwahed) · Fork : `ZeinebAbdelwahed/mlops-cours-2026` · Dataset : `german-credit` ·
Image en prod à la fin du lab : `ghcr.io/zeinebabdelwahed/mlops-cours-2026/scoring-api:2.1.8` · `@champion` : v15

## 1. Architecture

Trois chaînes indépendantes qui ne communiquent que par Git :

```
                      (1) CI                                   (2) CD                         (3) CT
 développeur ──git push/merge──► GitHub (main)  ◄──── lit toutes les 60 s ──── Argo CD (cluster kind)
                                     │                                            │ applique
                                     ▼                                            ▼
                         GitHub Actions « CI scoring »               KServe staging (scoring-staging)
                         ruff + pytest → build → test de fumée       KServe prod (scoring, canary)
                                     │ push image                                 ▲ tire l'image
                                     ▼                                            │
                                   GHCR  ────────────────────────────────────────┘
                     (ghcr.io/zeinebabdelwahed/…/scoring-api:2.1.N, package public)
                                     │
                         job deploy-staging : commit du nouveau tag
                         dans k8s/overlays/staging ──push──► GitHub (main)

 Evidently (detect) ──POST /drift + jeton──► webhook CT (poste, 127.0.0.1:8095)
        │ lot + rapport                          │ soumet                 │ après promotion : export ONNX,
        ▼                                        ▼                        │ commit packaging/model ──push──► GitHub (main)
   MLflow (scoring-drift)  ◄──lit data_uri── KFP ct-pipeline ──@champion──► MLflow Registry
                                          (ingest → validate → prepare → train → evaluate
                                           → register → promotion-gate)
```

| Flèche | Qui pousse | Qui tire |
|---|---|---|
| Code → GitHub | moi (PR fusionnée) ou le bot CT (commit `ct:` / `gitops(prod):`) | GitHub Actions (déclenché par `push` sur `main`) |
| Image → GHCR | GitHub Actions (`GITHUB_TOKEN`, `packages: write`) | les nœuds kind / Knative (package public, pull anonyme) |
| Tag de staging → Git | job `deploy-staging` (`github-actions[bot]`) | Argo CD (`scoring-staging`, poll 60 s) |
| Image + canary de prod → Git | `pipelines/gitops.py` (moi à l'étape 8, le bot à l'étape 9) | Argo CD (`scoring-prod`, prune + selfHeal) |
| Événement de dérive → webhook | `trigger_ct detect` (Evidently) | webhook FastAPI (vérifie le jeton) |
| Run KFP | webhook (API KFP via port-forward 8080) | Kubeflow Pipelines |
| Lot récent | `detect` le range dans MLflow (`runs:/…/batch/current.csv`) | composant `ingest-ct-data` (dans le cluster) |
| Modèle | `promotion-gate` pose `@champion` dans MLflow | `export_onnx.py` (bot) → `packaging/model/` → image |

Aucun composant externe n'a d'accès au cluster : GitHub ne voit pas le poste, c'est Argo CD qui tire Git (GitOps « pull »).

## 2. Temps du cycle

Démo de l'étape 9 (dérive ×1,5 sur `duration`, `credit_amount`, `age` ; webhook en mode automatique).
Sources : `date` avant `detect`, API KFP (run `3ff82815`), `git log --format='%ci %an | %s'`, journal du webhook.

| Étape | Début | Fin | Durée |
|---|---|---|---|
| Dérive détectée → run KFP lancé | 12:40:21 | 12:40:28 | 7 s |
| Pipeline CT (ingest → gate) | 12:40:28 | 12:44:10 | 3 min 42 s |
| Commit du modèle → image publiée (CI) | 12:44:24 | 12:46:12 | 1 min 48 s |
| Image publiée → staging servi | 12:46:12 | 12:48:01 | ≤ 1 min 49 s |
| Canary 10 % → promotion 100 % | 12:48:01 | 12:49:40 | 1 min 39 s |
| **Cycle complet** | 12:40:21 | 12:49:40 | **9 min 19 s** |

Entre la fin du run (12:44:10) et le commit `ct: scoring-model v15` (12:44:24) : 14 s d'export ONNX et de push.
« Staging servi » inclut la synchronisation Argo CD (≤ 70 s), le démarrage de la révision et la vérification du bot
(`/version` + une prédiction). « Canary » inclut l'application par Argo CD, le démarrage de la révision et
l'analyse naïve (50 prédictions, zéro erreur tolérée).

Commits du cycle, dans l'ordre :

```
12:44:24  ct: scoring-model v15 (run KFP 3ff82815, drift_share=0.15)
12:46:12  gitops(staging): ghcr.io/zeinebabdelwahed/mlops-cours-2026/scoring-api:2.1.8 [84bf0d5]   (github-actions[bot])
12:48:01  gitops(prod): canary 10% 2.1.8
12:49:40  gitops(prod): promote 100% 2.1.8
```

**Quelle étape domine, et comment la raccourcir ?** Le pipeline CT (3 min 42 s, 40 % du cycle) : chaque composant
crée un pod (driver + exécuteur), télécharge ses artefacts et réinstalle son contexte. Pistes : activer le cache KFP
sur les étapes dont les entrées n'ont pas changé, fusionner les petits composants (validate + prepare), garder l'image
du pipeline pré-chargée sur les nœuds. Ensuite viennent les attentes de synchronisation : un webhook GitHub → Argo CD
(impossible ici, GitHub ne voit pas le poste) supprimerait jusqu'à 60 s à chaque déploiement. La CI (1 min 48 s) est
déjà accélérée par le cache Docker `type=gha`. Avec un vrai modèle, l'entraînement dominerait largement.

## 3. Gates et décisions

**Gate de validation des données (Great Expectations, `validate-data`).** Premier essai de démo avec une dérive ×2
(`simulate --factor 2`, run `d453d166`) : run **arrêté** à `validate-data`, 2 règles en échec,
`expect_column_values_to_be_between(duration)` et `expect_column_values_to_be_between(age)` (valeurs hors des bornes
du contrat du Lab 3). Aucun entraînement, aucun commit, prod inchangée : la gate a joué son rôle. La démo a été
relancée avec ×1,5, qui reste dans le contrat.

**Gate de promotion (KFP, `promotion-gate`).** Règles : `roc_auc >= 0.7` (seuil de `dataset.yaml`) et coût métier du
challenger `<=` coût du champion actuel, les deux modèles évalués sur les **mêmes** données récentes, chacun à son seuil
de décision.

| Run | Challenger | Champion remplacé | roc_auc du challenger | Coût métier du challenger | Décision |
|---|---|---|---|---|---|
| `4f515b61` (étape 7, ×1,5) | v14 | v13 | 0.8094 | 108 | PROMUE |
| `3ff82815` (étape 9, ×1,5) | v15 | v14 | 0.8094 | 108 | PROMUE |

À l'étape 9, v15 a exactement les mêmes performances que v14 : le lot ×1,5 est le même qu'à l'étape 7, donc le modèle
ré-entraîné est équivalent. La règle `coût <= champion` (égalité acceptée, `max_cost_increase = 0`) l'a promu.
C'est correct mais discutable : voir section 4.

**Canary.** Mise en prod = un commit qui change l'image de `k8s/overlays/prod/kustomization.yaml` et
`canaryTrafficPercent` dans `canary.yaml`.

- Étape 8 (à la main, 2.0.0 → 2.1.7, modèle v13 → v14) : `gitops deploy --env prod --canary 10` →
  `PREV 90 / LATEST 10`. Mesure sur 200 requêtes `/version` : **183 × 2.0.0 et 17 × 2.1.7** (8,5 %, dans la plage
  attendue 12 à 28). Aucune erreur → `gitops promote` → `LATEST 100`, **200 réponses sur 200 en 2.1.7**.
- Étape 9 (bot, 2.1.7 → 2.1.8, modèle v14 → v15) : canary 10 %, analyse naïve de 50 prédictions sur la nouvelle
  révision, zéro erreur → promotion automatique en 1 min 39 s.

**Auto-réparation.** Un `kubectl patch` manuel de la prod (`canaryTrafficPercent: 50`) a été annulé par Argo CD
(selfHeal) en moins de 70 s : seul un commit change la prod.

## 4. Points d'attention

- **Sécurité du webhook.** Il lance des entraînements et pousse en prod : il exige un jeton (`CT_WEBHOOK_TOKEN`,
  32 caractères hexadécimaux, `openssl rand -hex 16`), sans quoi il répond 401 (vérifié). Il n'écoute que sur
  `127.0.0.1:8095`. Le jeton vit dans `.env`, ignoré par Git. Un délai minimal entre deux ré-entraînements
  (`CT_COOLDOWN_SECONDS`) évite les boucles.
- **Gate humaine ou automatique.** Ici tout est automatique jusqu'à la prod. La gate de promotion accepte un modèle
  *équivalent* (égalité de coût) : en production, il faudrait exiger une amélioration minimale, ou au moins ne pas
  republier un modèle identique, pour éviter des déploiements sans valeur. `CT_AUTO_PROMOTE=0` (canary laissé à 10 %)
  ou une PR ouverte par le bot réintroduisent une décision humaine.
- **Retour arrière.** Trois niveaux : trafic (`gitops canary 0`, la révision précédente est encore chaude, retour
  instantané), modèle (`gitops rollback` = canary 0 + `@champion` restauré grâce au tag `previous_champion`), Git
  (`git revert` d'un commit `gitops(prod)`, Argo CD réapplique). Chaque décision est un commit : `git log -- k8s/overlays/prod`
  donne l'historique complet de la prod.
- **Secrets : où sont-ils, qui les voit ?** `GITHUB_TOKEN` (éphémère, généré par GitHub pour chaque run, droits limités
  par job : `packages: write` pour l'image, `contents: write` pour le commit de staging). Jeton `gh` et clé SSH sur le
  poste : le bot pousse **avec mes droits**. Identifiants S3/MLflow dans `.env` et `.dvc/config.local` (non commités).
  Mot de passe admin Argo CD dans le Secret `argocd-initial-admin-secret`. Aucun secret dans Git (`make check-m6`
  le vérifie pour le jeton du webhook).
- **Incidents rencontrés pendant le lab (et corrections).**
  - `main` ne contenait que le commit initial (PR des labs précédents jamais fusionnées, branche du Lab 2 partie de
    `main` vide) : fusion des Labs 0 et 1 dans `main`, puis de `main` dans `lab/m5-serving` (conflit sur `params.yaml`
    résolu en gardant la version du Lab 5), puis PR des Labs 2 à 5.
  - Lint : 4 erreurs et 13 fichiers non formatés hérités des labs précédents, corrigés avec `ruff --fix` et
    `ruff format` pour que la CI passe.
  - `make kfp-ui` et `make pf` se terminent tout seuls (`pkill -f` tue le shell de `make`, dont la ligne de commande
    contient le motif) : port-forwards relancés directement avec `nohup kubectl port-forward …`.
  - KFP était en pause (14 deployments à 0 réplique, pour économiser la RAM au Lab 5) : `kubectl scale --replicas=1`.
  - `kubectl get applications` renvoyait « No resources found » : la ressource `applications.app.k8s.io` installée par
    Kubeflow masque celle d'Argo CD ; il faut écrire `applications.argoproj.io`.
  - Export du champion impossible : MLflow renvoie des URL pré-signées vers `http://s3.storage:8333` (nom interne au
    cluster). La signature inclut l'hôte, l'URL ne peut pas être réécrite : ajout de `127.0.0.1 s3.storage` dans
    `/etc/hosts` pour passer par le port-forward 8333.
  - Mémoire : WSL dispose de 7,6 Go pour kind + KServe + Argo CD + KFP + MLflow ; nombreux redémarrages de MLflow et
    SeaweedFS, port-forwards qui tombent. Argo CD installé avec les composants inutiles à 0 réplique.

## 5. Différences avec la prod

- **Bot dans le cluster** : le webhook tourne sur mon poste, avec mes droits Git et mes port-forwards. En prod : un
  Deployment dans le cluster, une GitHub App limitée à ce dépôt (pas mon compte), un ServiceAccount aux droits minimaux.
- **Secrets managés** : `.env` et fichiers locaux remplacés par un gestionnaire de secrets (Vault, External Secrets,
  Sealed Secrets), avec rotation et audit d'accès.
- **PR plutôt que commit direct** : le bot ouvrirait une PR (`gh pr create`) avec le rapport de la gate en
  description ; la prod n'avancerait qu'à la fusion, avec relecture et protection de `main`.
- **Environnements séparés** : staging et prod sur des clusters (ou au moins des nœuds) distincts, avec des quotas ;
  ici tout partage un seul cluster kind.
- **Synchronisation instantanée** : webhook GitHub → Argo CD au lieu d'un poll toutes les 60 s ; image privée avec
  `imagePullSecrets` plutôt qu'un package public.
- **Analyse du canary** : comparaison de métriques Prometheus (erreurs, latence, distribution des scores) entre les
  deux révisions, paliers 10 → 50 → 100 %, au lieu de 50 prédictions sans erreur (Lab 7).
- **Données réellement récentes et étiquetées** : ici la dérive est simulée (variables ×1,5) et les étiquettes sont
  connues. En prod, les étiquettes arrivent avec retard (issue du crédit) : il faut gérer ce délai et surveiller aussi
  la dérive des prédictions.
- **Métriques métier, audit et reprise** : suivi du coût métier en prod, journal d'audit de qui a promu quoi, sauvegardes
  de MLflow, du Registry et de la base KFP (plan de reprise), plusieurs réplicas des composants critiques.

## 6. Captures d'écran

Pas de captures jointes : l'état décrit est vérifiable en direct sur le cluster et dans le dépôt.

- Argo CD : `kubectl get applications.argoproj.io -n argocd` (scoring-prod et scoring-staging Synced / Healthy).
- KFP : expérience `scoring-ct`, run `3ff82815` réussi (7 étapes), artefact `report` de `promotion-gate`
  (version 15 PROMUE) ; run `d453d166` (dérive ×2) arrêté à `validate-data`.
- GitHub Actions : `gh run list --workflow ci.yml` (runs « CI scoring » en succès).
- KServe : `kubectl get isvc -A` et `python -m pipelines.gitops status` (staging et prod en 2.1.8, modèle v15).
- Vérification automatique : `make check-m6`, 18/18.
