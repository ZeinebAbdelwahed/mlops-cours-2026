# pipelines/scoring_pipeline.py — version de l'étape 3 (complétée aux étapes 5 et 6)
"""Pipeline KFP « scoring-pipeline » du Lab 2.

ingest -> validate -> prepare -> train -> evaluate
Compilation : python -m pipelines.scoring_pipeline   (écrit scoring_pipeline.yaml)
Les hyper-paramètres par défaut sont lus dans params.yaml AU MOMENT DE LA COMPILATION :
params.yaml reste la seule source des valeurs par défaut, comme pour dvc repro.
"""

from pathlib import Path

import yaml
from kfp import compiler, dsl

from pipelines.components import (
    MLFLOW_URI,
    evaluate_model,
    ingest_data,
    prepare_features,
    train_model,
    validate_data,
)

TRAIN = yaml.safe_load(Path("params.yaml").read_text(encoding="utf-8"))["train"]


@dsl.pipeline(
    name="scoring-pipeline",
    description="Credit scoring : ingest -> validate -> prepare -> train -> evaluate",
)
def scoring_pipeline(
    model_type: str = TRAIN["model"],
    C: float = TRAIN["C"],
    class_weight: str = str(TRAIN["class_weight"]),  # « none » accepté, comme au Lab 1
    n_estimators: int = TRAIN["n_estimators"],
    max_depth: str = str(TRAIN["max_depth"]),  # « none » accepté, comme au Lab 1
    learning_rate: float = TRAIN["learning_rate"],
    random_state: int = TRAIN["random_state"],
    mlflow_uri: str = MLFLOW_URI,
):
    ingest_task = ingest_data()

    validate_task = validate_data(raw_data=ingest_task.outputs["raw_data"])

    prep_task = prepare_features(raw_data=ingest_task.outputs["raw_data"])
    prep_task.after(validate_task)  # gate : pas de préparation sans validation réussie

    train_task = train_model(
        raw_data=ingest_task.outputs["raw_data"],
        train_data=prep_task.outputs["train_data"],
        test_data=prep_task.outputs["test_data"],
        model_type=model_type,
        C=C,
        class_weight=class_weight,
        n_estimators=n_estimators,
        max_depth=max_depth,
        learning_rate=learning_rate,
        random_state=random_state,
        mlflow_uri=mlflow_uri,
        kfp_run_id=dsl.PIPELINE_JOB_ID_PLACEHOLDER,  # remplacé par l'id du run KFP
    )

    evaluate_model(
        model=train_task.outputs["model"],
        test_data=prep_task.outputs["test_data"],
        run_id=train_task.outputs["Output"],
        mlflow_uri=mlflow_uri,
    )


if __name__ == "__main__":
    compiler.Compiler().compile(scoring_pipeline, package_path="scoring_pipeline.yaml")
