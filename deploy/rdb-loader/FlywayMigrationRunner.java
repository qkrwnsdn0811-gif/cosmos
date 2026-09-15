import org.flywaydb.core.Flyway;

/** One-off migration runner; uses the deployed application's exact Flyway jars. */
class FlywayMigrationRunner {
    private static Flyway configured(String location, String schema, boolean pending) {
        return Flyway.configure()
                .dataSource(System.getenv("COSMOS_MIGRATION_JDBC_URL"),
                        System.getenv("COSMOS_MIGRATION_DB_USER"),
                        System.getenv("COSMOS_MIGRATION_DB_PASSWORD"))
                .locations("filesystem:" + location)
                .schemas(schema).defaultSchema(schema).createSchemas(true)
                .target("5").cleanDisabled(true).validateMigrationNaming(true)
                .ignoreMigrationPatterns(pending ? new String[]{"*:pending"} : new String[0])
                .load();
    }

    public static void main(String[] args) {
        if (args.length != 3) {
            throw new IllegalArgumentException("Expected migration directory, schema, and validate/apply");
        }
        Flyway pending = configured(args[0], args[1], true);
        pending.validate();
        System.out.println("PRE_VALIDATE_OK schema=" + args[1]);
        if (!"apply".equals(args[2])) {
            return;
        }
        int migrated = pending.migrate().migrationsExecuted;
        Flyway strict = configured(args[0], args[1], false);
        strict.validate();
        int repeated = strict.migrate().migrationsExecuted;
        strict.validate();
        String version = strict.info().current().getVersion().getVersion();
        if (!"5".equals(version) || repeated != 0) {
            throw new IllegalStateException("Expected schema V5 and an idempotent second migrate");
        }
        System.out.println("MIGRATION_OK schema=" + args[1] + " version=" + version
                + " migrations=" + migrated + " repeated_migrations=" + repeated);
    }
}
