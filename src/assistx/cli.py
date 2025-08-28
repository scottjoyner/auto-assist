
import click
from .neo4j_client import Neo4jClient
from .pipeline_ingest import ingest_dir
from .pipeline_summarize import summarize_conversation, summarize_since_days
from .pipeline_execute import execute_ready

@click.group()
def cli():
    pass

@cli.command("init")
def init_cmd():
    neo = Neo4jClient(); neo.ensure_schema(); neo.close(); click.echo("Schema ensured.")

@cli.command("ingest")
@click.option("--src", required=True, type=click.Path(exists=True, file_okay=False))
def ingest_cmd(src):
    neo = Neo4jClient(); ingest_dir(src, neo); neo.close()

@cli.command("summarize")
@click.option("--since-days", default=7, show_default=True, type=int)
def summarize_cmd(since_days):
    neo = Neo4jClient(); summarize_since_days(neo, days=since_days); neo.close()

@cli.command("execute")
@click.option("--limit", default=5, show_default=True, type=int)
@click.option("--dry-run", is_flag=True, default=False)
def execute_cmd(limit, dry_run):
    neo = Neo4jClient(); execute_ready(neo, limit=limit, dry_run=dry_run); neo.close()

@cli.command("approve")
@click.option("--limit", default=25, show_default=True, type=int, help="Number of REVIEW tasks to list/approve")
@click.option("--all", "approve_all", is_flag=True, default=False, help="Approve all listed tasks without prompt")
def approve_cmd(limit, approve_all):
    neo = Neo4jClient(); tasks = neo.get_review_tasks(limit=limit)
    if not tasks: click.echo("No REVIEW tasks."); neo.close(); return
    for t in tasks:
        click.echo(f"[REVIEW] {t['id']} | {t.get('title')} | prio={t.get('priority')} conf={t.get('confidence')}")
        if approve_all or input("Approve? [y/N] ").lower().startswith("y"):
            neo.update_task_status(t["id"], "READY")
    neo.close()

@cli.command("run-all")
@click.option("--src", required=True, type=click.Path(exists=True, file_okay=False))
@click.option("--since-days", default=365, show_default=True)
@click.option("--execute", is_flag=True, default=False)
@click.option("--dry-run", is_flag=True, default=False)
def run_all(src, since_days, execute, dry_run):
    neo = Neo4jClient(); neo.ensure_schema(); ingest_dir(src, neo); summarize_since_days(neo, days=since_days)
    if execute: execute_ready(neo, limit=10, dry_run=dry_run)
    neo.close()

@cli.command("eval")
@click.option("--gold", required=True, type=click.Path(exists=True, file_okay=False), help="Dir of gold JSON files")
@click.option("--pred", required=True, type=click.Path(exists=True, file_okay=False), help="Dir of predictions (summary+tasks JSON by id)")
def eval_cmd(gold, pred):
    from .eval import run_eval
    res = run_eval(gold, pred)
    import json as _json; click.echo(_json.dumps(res, indent=2))

@cli.command("export-pred")
@click.option("--out", "out_dir", required=True, type=click.Path(file_okay=False), help="Directory to write eval/pred JSON files")
@click.option("--limit", default=100, show_default=True, type=int)
def export_pred_cmd(out_dir, limit):
    from .exporter import export_predictions
    export_predictions(out_dir=out_dir, limit=limit)
    import click as _click; _click.echo(f"Exported predictions to {out_dir}")

if __name__ == "__main__":
    cli()
