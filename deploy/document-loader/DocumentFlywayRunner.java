import org.flywaydb.core.Flyway;

/** Apply the reviewed document migration with the application's Flyway libraries. */
class DocumentFlywayRunner {
    private static Flyway configured(String location, boolean pending) {
        return Flyway.configure()
                .dataSource(System.getenv("DOCUMENT_MIGRATION_JDBC_URL"),
                        System.getenv("DOCUMENT_MIGRATION_DB_USER"),
                        System.getenv("DOCUMENT_MIGRATION_DB_PASSWORD"))
                .locations("filesystem:" + location)
                .schemas("public").defaultSchema("public").createSchemas(false)
                .target("6").cleanDisabled(true).validateMigrationNaming(true)
                .initSql("SET lock_timeout='5s'; SET statement_timeout='5min'")
                .ignoreMigrationPatterns(pending ? new String[]{"*:pending"} : new String[0])
                .load();
    }

    public static void main(String[] args) {
        if (args.length != 2 || !(args[1].equals("validate") || args[1].equals("apply"))) {
            throw new IllegalArgumentException("Expected migration directory and validate/apply");
        }
        Flyway pending = configured(args[0], true);
        pending.validate();
        System.out.println("DOCUMENT_PRE_VALIDATE_OK");
        if (args[1].equals("validate")) return;
        int migrated = pending.migrate().migrationsExecuted;
        Flyway strict = configured(args[0], false);
        strict.validate();
        int repeated = strict.migrate().migrationsExecuted;
        strict.validate();
        String version = strict.info().current().getVersion().getVersion();
        if (!version.equals("6") || repeated != 0) {
            throw new IllegalStateException("Expected schema V6 and an idempotent second migrate");
        }
        System.out.println("DOCUMENT_MIGRATION_OK version=" + version
                + " migrated=" + migrated + " repeated=" + repeated);
    }
}
