# Rapport — Lab 5 : serving avec KServe

Binôme : Zeineb · Mohammed · Dataset : german-credit (`name` de dataset.yaml) · Modèle : `scoring-model` v13 ·
Seuil de décision servi : 0.3182

## 1. Résumé

Le modèle de scoring (image FastAPI + ONNX du Lab 4, `localhost:5001/scoring-api`) est servi par
l'InferenceService `scoring-model` de KServe 0.21 en mode Knative, derrière la passerelle Kourier
exposée sur `http://localhost:30080` (routage par en-tête `Host`), dans le cluster kind du cours.
L'autoscaler de Knative (KPA) pilote le nombre de pods sur le débit (`scaleMetric: rps`,
`scaleTarget: 200`) de 0 à 5 pods. En fin de lab, la v2.0.0 (même modèle v13, route `/version`
en plus) a été déployée en canary 90/10 puis promue : elle sert 100 % du trafic (révision 00003),
la révision 00002 (v1.0.0) restant disponible pour un rollback.

## 2. Benchmark de latence

Requêtes `/predict` seulement, vraies lignes de `data/processed/test.csv` ; runs 500 et 1000
avec `--processes 4` pour que Locust ne soit pas lui-même le goulot.

| Run | Requêtes | RPS | P50 (ms) | P95 (ms) | P99 (ms) | Erreurs (%) |
|---|---|---|---|---|---|---|
| run_100 | 55185 | 479.5 | 4 | 7 | 18 | 0.00 |
| run_500 | 352429 | 2024.9 | 29 | 95 | 510 | 0.00 |
| run_1000 | 383676 | 2201.7 | 240 | 350 | 500 | 0.00 |

Erreurs : 0 au run 100, 1 (502) au run 500, 12 (502) au run 1000, soit 0,0003 % et 0,003 %
(arrondis à 0.00 dans le tableau).

Point de saturation : le service sature entre 500 et 1000 utilisateurs. En doublant la charge, le
débit ne gagne que 9 % (2025 → 2202 req/s) alors que la médiane est multipliée par 8 (29 → 240 ms) :
les 5 pods (maxReplicas) sont pleins et les requêtes supplémentaires font la queue. RPS max mesuré :
≈ 2 200 à 2 500 req/s avec 5 pods, soit ≈ 450 à 500 req/s par pod. C'est cohérent avec la loi de
Little : 1000 utilisateurs / 2202 req/s ≈ 0,45 s par cycle = 0,2 s d'attente Locust + ≈ 0,25 s
de latence (le P50 mesuré est 240 ms).
Le P99 du run 500 (510 ms) est déjà élevé car il inclut la montée en charge (scale-up de 1 à 5 pods
et démarrage à froid, le service étant à 0 pod au début du run) ; au run 1000, le service était
déjà chaud, mais c'est toute la distribution qui se décale vers le haut.
Limite de la mesure : Locust tourne sur la même machine que le cluster kind et lui prend du CPU.

## 3. Scale-up observé

| Run | Pods (max observé) | CPU moyen par pod | Temps pour atteindre ce nombre de pods |
|---|---|---|---|
| run_100 | non relevé (≈ 4 attendus : 480 req/s / 140 par pod) | non relevé | non relevé |
| run_500 (mesure du 11/11, `reports/m5/scaleup_500.txt`) | 5 (maxReplicas) | ≈ 1,2 CPU/pod (≈ 1 CPU pour l'API, à sa limite, + queue-proxy) | 2 pods en ≈ 7 s, 5 pods en ≈ 11 s |
| run_1000 | 5 (plafond maxReplicas, déjà atteint à 500) | non relevé (pods saturés, cf. latence) | non relevé |

Mesure de scale-up (11/11, run de 90 s à 500 utilisateurs, départ de 1 pod car `minReplicas: 1` après le canary) : le KPA passe de 1 à 5 pods en ≈ 11 s (mode panique de Knative). Après l'arrêt de la charge, les 5 pods restent ≈ 90 s (fenêtre de 60 s + `scale-down-delay` de 30 s) avant de redescendre. Ce run court a donné 1 208 req/s, P50 130 ms (la boucle de mesure `kubectl` tournait sur la même machine) : on garde les runs du tableau 2 comme référence.

Scale-to-zero : délai observé avant 0 pod entre ≈ 113 s et 154 s (le run 3, lancé 1 min 53 s après
la fin du run 2, a démarré à chaud ; le run 2, lancé 2 min 34 s après la fin du run 1, a démarré à
froid), cohérent avec la théorie : fenêtre de 60 s + `scale-down-delay` de 30 s + délai de grâce
Knative de 30 s ≈ 120 s.
Démarrage à froid (`curl -w`) : 6,28 s pour le premier appel, puis 0,004 s pour le suivant (≈ 1 500 ×
plus lent). On le retrouve dans Locust : max de 6,0 s et P99.9 de 2,8 s au run 100, uniquement sur
les toutes premières requêtes.

## 4. Analyse canary

- Répartition observée sur 200 requêtes : 190 × 1.0.0 / 10 × 2.0.0 (5 %).
- Répartition observée sur 1000 requêtes : 897 × 1.0.0 / 103 × 2.0.0 (10,3 %).
- Cohérence avec 90/10 : sur 200 requêtes, on attend 20 ± 4,2 (écart-type d'une loi binomiale
  n = 200, p = 0,1) ; 10 est à ≈ 2,4 écarts-types, un tirage rare (≈ 1 %) mais possible. La mesure
  sur 1000 requêtes (attendu 100 ± 9,5) donne 103, soit 0,3 écart-type : le split est bien 90/10.
  Conclusion : un petit échantillon donne un pourcentage bruité ; il faut assez de trafic pour
  juger un canary.
- Les adresses dédiées (`latest-…` et `prev-…`, annotation `enable-tag-routing`) ont permis de
  tester chaque version isolément : la v2 répond `{"version":"2.0.0","model_version":"13"}`.
- Décision : promotion (`canaryTrafficPercent: 100`). Justification : même modèle (v13) et même
  seuil (0.3182) que la v1, donc mêmes prédictions ; aucune erreur observée sur la v2 ; la nouvelle
  route `/version` fonctionne. Limite : la promotion est un `kubectl patch` manuel, donc le cluster ne
  correspond plus au manifest dans Git (qui indique encore 10 %) ; les Modules 6 et 7 automatisent
  cette décision (GitOps, analyse des métriques, rollback automatique).

## 5. Points d'amélioration

- Plusieurs workers uvicorn par pod : le goulot est l'unique worker Python, saturé bien avant la
  limite d'1 CPU ; 2 à 4 workers augmenteraient le débit par pod.
- Augmenter `maxReplicas` (5 → 10) si la machine ou le cluster le permet : c'est le plafond atteint
  au run 1000.
- Ajuster la cible de l'autoscaler : un pod tient ≈ 450 req/s, alors que Knative vise 140 req/s par pod
  (70 % de 200) ; on peut relever la cible pour utiliser moins de pods à charge égale, ou passer sur
  `scaleMetric: concurrency`, qui réagit directement aux requêtes en attente.
- `minReplicas: 1` aux heures ouvrées : le démarrage à froid (6 s) est inacceptable pour un client
  en plein parcours de demande de crédit ; le scale-to-zero reste intéressant la nuit.
- Batching : l'API accepte jusqu'à 1000 demandes par requête ; regrouper les demandes divise le coût
  HTTP/JSON par prédiction (le modèle ONNX ne coûte que ≈ 0,07 ms).
- Runtime standard (Triton) pour un serveur plus optimisé, à condition de garder le seuil métier
  cohérent côté client.
- Faire tourner Locust sur une autre machine que le cluster pour une mesure non biaisée.
- Automatiser le canary (progression et rollback pilotés par Git et par les métriques).

## 6. (Optionnel) Comparatif des serveurs et bonus batch

Non réalisé (étape 8 optionnelle : image Triton de ≈ 6 Go, ressources du poste limitées).
