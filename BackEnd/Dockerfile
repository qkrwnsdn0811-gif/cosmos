# syntax=docker/dockerfile:1
FROM eclipse-temurin:21-jdk-jammy AS build

WORKDIR /workspace
COPY gradlew build.gradle settings.gradle ./
COPY gradle ./gradle
RUN sed -i 's/\r$//' gradlew && chmod +x gradlew
COPY src ./src

# Jenkins runs this stage's test command against isolated PostgreSQL and Redis.
# Image packaging itself never requires access to a database.
RUN ./gradlew --no-daemon bootJar -x test
CMD ["./gradlew", "--no-daemon", "test"]

FROM eclipse-temurin:21-jre-jammy AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system --gid 10001 cosmos \
    && useradd --system --uid 10001 --gid cosmos --no-create-home cosmos

WORKDIR /app
COPY --from=build --chown=10001:10001 /workspace/build/libs/*.jar /app/app.jar

ENV SPRING_PROFILES_ACTIVE=prod \
    SERVER_PORT=18081 \
    TZ=UTC

USER 10001:10001
EXPOSE 18081

HEALTHCHECK --interval=30s --timeout=5s --start-period=90s --retries=3 \
    CMD curl --fail --silent --show-error "http://127.0.0.1:${SERVER_PORT}/actuator/health" > /dev/null || exit 1

ENTRYPOINT ["java", "-jar", "/app/app.jar"]
