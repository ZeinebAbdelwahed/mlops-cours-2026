# Rapport Lab 2 : KFP vs Airflow

## Contexte

J'ai migré le pipeline DVC du Lab 1 (scoring de crédit, dataset german-credit, 1000 lignes, 20 variables) vers deux orchestrateurs sur le cluster kind. Le pipeline compte 6 steps : ingest, validate, prepare, train, evaluate, register. Les deux versions utilisent la même image projet (scoring-pipeline:1.0.1) et les mêmes commandes `python -m src.<étape>`. Le modèle atteint un roc_auc de 0.8094, et chaque run produit un run MLflow (`launched_by=kfp` ou `airflow`).

## Résultats quantitatifs

| Mesure | KFP | Airflow |
|---|---|---|
| Durée d'un run complet | 2 min 45 s (sans cache) | environ 2 min |
| Durée avec cache | 2 min 14 s (gain d'environ 19 %) | pas de cache natif |
| Lignes de code | 289 (components.py + scoring_pipeline.py) | 62 (scoring_dag.py) |
| Mémoire | 3,6 Go (cluster entier au repos : kind + KFP + MLflow + S3) | environ 4 Go (ordre de grandeur donné par le lab, non mesuré) |

Les 62 lignes du DAG sont trompeuses : le transport des fichiers via S3 est dans `pipelines/stage.py` (environ 150 lignes), que KFP n'utilise qu'à moitié. Les durées des deux runs sont proches, donc sur ce petit dataset, le choix de l'outil ne se joue pas sur la vitesse.

## Analyse qualitative

**Developer experience.** KFP demande plus de code, mais fournit gratuitement des artefacts typés (Dataset, Model, Metrics), des métriques et une matrice de confusion dans l'UI, ainsi que le lineage en un clic. Avec Airflow, le DAG est court et lisible, mais je dois gérer moi-même le passage de fichiers entre pods (dossier de travail S3, XCom pour les petites valeurs). Le test local `kfp.local` m'a permis de repérer les erreurs de composants en 30 secondes avant de déployer.

**Scaling.** Dans les deux cas, un step correspond à un pod, avec des ressources réglables (`set_memory_limit` côté KFP, `container_resources` côté Airflow). Airflow demande plus de services à faire tourner (apiserver, scheduler, worker, Postgres, Redis), et son installation a été la partie la plus lourde de mon lab, avec des téléchargements interrompus et une RAM saturée (WSL limité à environ 7,6 Go).

**Debugging.** Mon premier run Airflow a échoué sur `prepare` : `No module named src.prepare`. Les logs du pod (`kubectl logs`) ont montré que l'image 1.0.0 était incomplète, et le passage à 1.0.1 a réglé le problème. Côté KFP, l'upload du pipeline a donné une erreur 409 car il existait déjà ; il fallait ajouter une version. Ces erreurs étaient claires une fois les logs lus, mais le message de l'UI KFP seul n'était pas explicite.

## Recommandation

Je choisirais KFP pour un projet centré sur le machine learning : artefacts typés, caching par étape et lineage sont exactement ce dont un pipeline d'entraînement a besoin. Je choisirais Airflow si l'équipe orchestre déjà des tâches variées (ETL, BI, rapports planifiés) et veut un seul outil pour tout, à condition d'accepter de gérer le transport des données. Dans les deux cas, il faut versionner l'image à chaque changement de code.

## Limites de ma comparaison

Le dataset est petit (1000 lignes), donc le caching n'a donné que 19 % de gain : il serait bien plus utile avec des étapes data coûteuses. Je n'ai exécuté qu'un seul run par outil, sans mesure statistique, sur un cluster kind local avec peu de RAM. La mémoire d'Airflow n'a pas été mesurée : la valeur indiquée est l'ordre de grandeur du lab. Je n'ai pas testé KFP sur un vrai cluster managé, ni le KubernetesExecutor d'Airflow, ni le parallélisme de plusieurs entraînements.
