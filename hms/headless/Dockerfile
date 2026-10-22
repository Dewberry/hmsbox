ARG HMS_VERSION=4.14

# First stage for building the small Java program that wraps the HMS run.
FROM debian:trixie-slim AS javabuilder

RUN apt-get update && apt-get install -y --no-install-recommends \
  ca-certificates curl openjdk-21-jdk-headless && rm -rf /var/lib/apt/lists/*
COPY ./RunHMS.java /tmp/RunHMS.java
RUN javac --release 8 -d /tmp/classes /tmp/RunHMS.java && jar cfe /tmp/hms-compute.jar dewberry.RunHMS -C /tmp/classes .

# Try multiple download URLs, there may be some inconsistency between different releases.
ARG HMS_VERSION
COPY ./hms_unzip.sh /usr/local/bin/hms_unzip.sh
RUN url="https://www.hec.usace.army.mil/nexus/repository/maven-public/mil/army/usace/hec/hec-hms/${HMS_VERSION}-linux64/hec-hms-${HMS_VERSION}" \
  && (curl --fail --location --retry 2 "${url}-linux64.tar.gz" -o /tmp/hms.tar.gz \
  || curl --fail --location --retry 2 "${url}-linux64-linux64.tar.gz" -o /tmp/hms.tar.gz) \
  && bash /usr/local/bin/hms_unzip.sh /tmp/hms.tar.gz "/hec-hms/HEC-HMS-${HMS_VERSION}"


# Second stage for downloading and configuring HMS.
FROM debian:trixie-slim AS prod
ARG HMS_VERSION
ENV HMS_VERSION=${HMS_VERSION}

LABEL org.opencontainers.image.title="HEC-HMS Headless Container" \
      org.opencontainers.image.description="Docker container for running HEC-HMS hydrological simulations in headless mode" \
      org.opencontainers.image.version="${HMS_VERSION}" \
      org.opencontainers.image.vendor="Dewberry" \
      org.opencontainers.image.source="https://github.com/Dewberry/hmsbox" \
      org.opencontainers.image.documentation="https://github.com/Dewberry/hmsbox/tree/main/hms/headless"

RUN apt-get update && apt-get install -y --no-install-recommends \
    libxrender1 libxtst6 libxi6 libfreetype6 libgfortran5 libfontconfig1 \
    && rm -rf /var/lib/apt/lists/* \
  && useradd -m -u 1000 hms \
  && mkdir -p /app && chown hms:hms /app

COPY --from=javabuilder --chown=hms:hms /hec-hms /hec-hms
RUN rm -f /hec-hms/lib && mkdir -p /hec-hms/lib
COPY --from=javabuilder /tmp/hms-compute.jar /hec-hms/lib/hms-compute.jar

# Verify HMS directory structure
RUN echo "=== HMS Directory Structure ===" && \
    ls -la /hec-hms/ && \
    echo "=== HEC-HMS subdirectories ===" && \
    find /hec-hms -maxdepth 2 -type d | head -20 && \
    echo "=== Checking for hms.jar ===" && \
    find /hec-hms -name "hms.jar" && \
    echo "=== Checking for lib directory ===" && \
    ls -la /hec-hms/HEC-HMS-* 2>/dev/null | head -20 || echo "HEC-HMS directory not found with expected naming" && \
    echo "=== Searching for GDAL native libraries ===" && \
    find /hec-hms -name "*gdal*jni*" -o -name "libgdal*" | head -20 && \
    echo "=== Checking bin directories ===" && \
    find /hec-hms -type d -name "bin" -exec ls -la {} \;

# Set paths to HMS and its Java runtime.
ENV HMS_HOME=/hec-hms/HEC-HMS-${HMS_VERSION}
ENV JAVA_EXE=$HMS_HOME/jre/bin/java
ENV JAVA_HOME=$HMS_HOME/jre
RUN test -x ${JAVA_EXE}

# Set misc paths.
ENV PATH=$HMS_HOME/bin/taudem:$HMS_HOME/bin/mpi:$HMS_HOME/bin:$HMS_HOME/jre/bin/:$HMS_HOME/bin/:$HMS_HOME/jre/lib/:$PATH
ENV GDAL_DATA=$HMS_HOME/bin/gdal/gdal-data
ENV PROJ_LIB=$HMS_HOME/bin/gdal/proj
ENV CLASSPATH=/hec-hms/lib/*:$HMS_HOME/*:$HMS_HOME/lib/*:$HMS_HOME/lib/hec/*:$HMS_HOME/jre/lib/*

# Configure HMS.
COPY ./hms_settings/ /etc/hms-logging/
RUN cat > /etc/hms-java.options <<EOF
  -XX:+ExitOnOutOfMemoryError
  -XX:MaxRAMPercentage=75
  -XX:+UseContainerSupport
  -Djava.library.path=${HMS_HOME}/bin:${HMS_HOME}/bin/javaHeclib:${HMS_HOME}/bin/gdal:${JAVA_HOME}/lib:${JAVA_HOME}/lib/server
  -Djava.util.logging.config.file=/etc/hms-logging/logging.properties
  -Dorg.slf4j.simpleLogger.defaultLogLevel=error
  -Dorg.slf4j.simpleLogger.showDateTime=false
  -Dorg.slf4j.simpleLogger.showThreadName=false
  -Dlogback.configurationFile=/etc/hms-logging/logback.xml
  -Dlog4j.configurationFile=/etc/hms-logging/log4j2.xml
  -Dlog4j2.configurationFile=/etc/hms-logging/log4j2.xml
  -Dlog4j2.statusLoggerLevel=OFF
  -Djava.awt.headless=true
  -Dprism.order=sw
  -Dprism.text=t2k
  -Dprism.verbose=false
  -Dglass.platform=Monocle
  -Dmonocle.platform=Headless
  -Dtestfx.robot=glass
  -Dtestfx.headless=true
EOF
ENV HDF5_DISABLE_ERROR_STACK=1

RUN cat > /usr/local/bin/run-hms.sh <<'SCRIPT'
#!/bin/bash
exec "${JAVA_EXE}" @/etc/hms-java.options dewberry.RunHMS "$@"
SCRIPT
RUN chmod +x /usr/local/bin/run-hms.sh

WORKDIR /app
USER hms

ENTRYPOINT ["/usr/local/bin/run-hms.sh"]