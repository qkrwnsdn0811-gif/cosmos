#!/usr/bin/env python3
"""Run V1-V5 using the running backend's Flyway libraries on the database host.

Requires Python 3.11+, local Java 21 (including jdk.compiler), Docker via passwordless sudo,
and both companion FlywayMigrationRunner.java and the exact migration files.
Default mode validates only. --apply makes a restricted full-database pg_dump,
migrates the selected schema, validates it, and verifies an idempotent retry.
Database credentials remain in memory and the child process environment.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile


NAMES = (
    "V1__create_initial_schema.sql", "V2__add_metric_window_type.sql",
    "V3__add_company_metric_lookup_index.sql", "V4__personalize_relationship_score.sql",
    "V5__add_graph_snapshot_load_receipt.sql",
)
TABLES = ("company", "company_relationship", "graph_snapshot",
          "relationship_score_current", "relationship_score_history")


def command(args, **kwargs):
    result = subprocess.run(args, capture_output=True, **kwargs)
    if result.returncode:
        # Docker errors can include environment values; callers get a safe failure.
        raise RuntimeError(f"{args[0]} command failed with exit {result.returncode}")
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--migration-dir", required=True, type=Path)
    parser.add_argument("--work-dir", required=True, type=Path)
    parser.add_argument("--schema", default="public")
    parser.add_argument("--postgres-container", default="cosmos-postgres-1")
    parser.add_argument("--backend-container", default="cosmos-backend-1")
    parser.add_argument("--database", default="cosmos")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", args.schema):
        parser.error("schema must be a simple lowercase PostgreSQL identifier")
    os.umask(0o077)
    work = args.work_dir.resolve()
    work.mkdir(parents=True, exist_ok=True, mode=0o700)
    if work.stat().st_mode & 0o077:
        raise RuntimeError("work directory must not be accessible to group or other users")
    migration_dir = args.migration_dir.resolve(strict=True)
    if sorted(path.name for path in migration_dir.glob("V*.sql")) != sorted(NAMES):
        raise RuntimeError("migration directory must contain exactly the reviewed V1-V5 files")
    runner = Path(__file__).with_name("FlywayMigrationRunner.java").resolve(strict=True)
    jar = work / "deployed-app.jar"
    command(["sudo", "-n", "docker", "cp", args.backend_container + ":/app/app.jar", str(jar)])
    command(["sudo", "-n", "chown", f"{os.getuid()}:{os.getgid()}", str(jar)])
    jar.chmod(0o600)
    libraries = work / "libs"
    libraries.mkdir(exist_ok=True, mode=0o700)
    hashes = {}
    versions = []
    with zipfile.ZipFile(jar) as deployed:
        for name in NAMES[:4]:
            actual = (migration_dir / name).read_bytes()
            expected = deployed.read("BOOT-INF/classes/db/migration/" + name)
            if actual != expected:
                raise RuntimeError(f"deployed migration differs from supplied file: {name}")
            hashes[name] = hashlib.sha256(actual).hexdigest()
        for entry in deployed.namelist():
            if entry.startswith("BOOT-INF/lib/") and entry.endswith(".jar"):
                (libraries / Path(entry).name).write_bytes(deployed.read(entry))
                if Path(entry).name.startswith(("flyway-core-", "flyway-database-", "postgresql-")):
                    versions.append(Path(entry).name)
    # Include only this deployed jar's libs, even when a work directory is reused.
    classpath = ":".join(str(libraries / Path(name).name) for name in deployed.namelist()
                         if name.startswith("BOOT-INF/lib/") and name.endswith(".jar"))
    info = json.loads(command(["sudo", "-n", "docker", "inspect", args.postgres_container]))[0]
    settings = dict(value.split("=", 1) for value in info["Config"]["Env"] if "=" in value)
    user = settings.get("POSTGRES_USER", "postgres")
    password = settings.get("POSTGRES_PASSWORD")
    if password is None and settings.get("POSTGRES_PASSWORD_FILE"):
        password = command(["sudo", "-n", "docker", "exec", args.postgres_container,
                            "cat", settings["POSTGRES_PASSWORD_FILE"]]).decode().strip()
    if not password:
        raise RuntimeError("PostgreSQL container does not provide an admin password")
    networks = info["NetworkSettings"]["Networks"]
    address = next((network["IPAddress"] for network in networks.values() if network["IPAddress"]), None)
    if not address:
        raise RuntimeError("PostgreSQL container has no reachable Docker address")

    def sql(query):
        value = command(["sudo", "-n", "docker", "exec", args.postgres_container,
                         "psql", "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1",
                         "-U", user, "-d", args.database, "-c", query]).decode().strip()
        return json.loads(value)

    def snapshot():
        existing = sql("SELECT COALESCE(json_agg(table_name), '[]') FROM information_schema.tables "
                       f"WHERE table_schema='{args.schema}';")
        counts = {table: sql(f'SELECT to_json(count(*)) FROM "{args.schema}"."{table}";')
                  for table in TABLES if table in existing}
        history = sql("SELECT COALESCE(json_agg(row_to_json(h) ORDER BY installed_rank), '[]') FROM "
                      f'"{args.schema}".flyway_schema_history h;') if "flyway_schema_history" in existing else []
        return {"counts": counts, "history": history}

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    before = snapshot()
    evidence = {"schema": args.schema, "libraries": versions, "deployed_migration_sha256": hashes,
                "before": before, "apply": args.apply}
    if args.apply:
        backup = work / f"{args.database}-{args.schema}-{stamp}.dump"
        with backup.open("xb") as destination:
            result = subprocess.run(["sudo", "-n", "docker", "exec", args.postgres_container,
                                     "pg_dump", "-U", user, "-d", args.database,
                                     "--format=custom", "--no-owner", "--no-acl"],
                                    stdout=destination, stderr=subprocess.PIPE)
        if result.returncode or backup.stat().st_size == 0:
            raise RuntimeError("database backup failed; migration was not started")
        # Check that pg_restore can parse the full dump archive header/TOC.
        with backup.open("rb") as source:
            verified = subprocess.run(["sudo", "-n", "docker", "exec", "-i", args.postgres_container,
                                       "pg_restore", "--list"], stdin=source,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        if verified.returncode:
            raise RuntimeError("backup archive verification failed; migration was not started")
        with backup.open("rb") as source:
            backup_hash = hashlib.file_digest(source, "sha256").hexdigest()
        evidence["backup"] = {"path": str(backup), "bytes": backup.stat().st_size,
                              "sha256": backup_hash}
    environment = dict(os.environ, COSMOS_MIGRATION_JDBC_URL=f"jdbc:postgresql://{address}:5432/{args.database}",
                       COSMOS_MIGRATION_DB_USER=user, COSMOS_MIGRATION_DB_PASSWORD=password)
    result = subprocess.run(["java", "-cp", classpath, str(runner), str(migration_dir),
                             args.schema, "apply" if args.apply else "validate"],
                            env=environment, capture_output=True)
    log = (result.stdout + result.stderr).decode(errors="replace").replace(password, "[REDACTED]")
    (work / f"flyway-{args.schema}-{stamp}.log").write_text(log)
    if result.returncode:
        print(log)
        raise RuntimeError("Flyway failed; inspect the restricted operation log")
    after = snapshot()
    evidence["after"] = after
    if before["counts"] and before["counts"] != after["counts"]:
        raise RuntimeError("existing service table counts changed unexpectedly")
    prior = {row["version"]: row for row in before["history"] if row.get("version")}
    current = {row["version"]: row for row in after["history"] if row.get("version")}
    if any(current.get(version) != row for version, row in prior.items()):
        raise RuntimeError("existing Flyway history rows changed unexpectedly")
    evidence_path = work / f"migration-{args.schema}-{stamp}.json"
    evidence_path.write_text(json.dumps(evidence, indent=2))
    print(log)
    print(json.dumps({"evidence": str(evidence_path), "schema": args.schema, "apply": args.apply}))


if __name__ == "__main__":
    main()
