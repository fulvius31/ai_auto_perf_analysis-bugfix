"""Optional MLflow export for pipeline run data."""
from __future__ import annotations

from auto_bug_fix.tracker import PipelineRun


def export_to_mlflow(
    run: PipelineRun,
    json_path: str,
    experiment_name: str = "bug-fix-porting",
) -> str:
    import mlflow

    mlflow.set_experiment(experiment_name)
    with mlflow.start_run(run_name=f"{run.issue_id}") as mlflow_run:
        mlflow.log_param("issue_id", run.issue_id)
        mlflow.log_param("run_id", run.run_id)
        mlflow.log_param("outcome", run.outcome)

        config = run.config
        if isinstance(config, dict):
            for key in ["source_branch", "target_branch", "source_fix_commit", "repo_path"]:
                if key in config:
                    mlflow.log_param(key, config[key])

        mlflow.log_metric("total_duration_s", run.total_duration_s)
        mlflow.log_metric("total_cost_usd", run.total_cost_usd)
        mlflow.log_metric("total_queries", run.total_queries)
        mlflow.log_metric("total_input_tokens", run.total_input_tokens)
        mlflow.log_metric("total_output_tokens", run.total_output_tokens)

        for i, phase in enumerate(run.phases):
            phase_key = phase.phase.replace(" ", "_").replace("—", "").lower()
            mlflow.log_metric(f"{phase_key}_duration_s", phase.duration_s)
            mlflow.log_metric(f"{phase_key}_queries", len(phase.queries))

            phase_cost = sum(q.cost_usd for q in phase.queries)
            mlflow.log_metric(f"{phase_key}_cost_usd", phase_cost)

            for j, q in enumerate(phase.queries):
                mlflow.log_metric(f"{phase_key}_q{j}_duration_s", q.duration_s)
                mlflow.log_metric(f"{phase_key}_q{j}_tokens", q.input_tokens + q.output_tokens)

        mlflow.log_artifact(json_path)

        return mlflow_run.info.run_id
